# bot.py

import os
import asyncio
import logging
from datetime import datetime
from io import BytesIO
import re
import sys

from aiogram import Bot, Dispatcher, types
from aiogram.dispatcher import FSMContext
from aiogram.dispatcher.filters.state import State, StatesGroup
from aiogram.contrib.fsm_storage.memory import MemoryStorage
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.utils import executor
from aiogram.dispatcher.filters import Text

import aiosqlite
import gspread
from google.oauth2.service_account import Credentials
from googleapiclient.discovery import build

import openai

from dotenv import load_dotenv

# Настройка логирования
logging.basicConfig(
    level=logging.DEBUG,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler("bot_debug.log"),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)

# Загрузка переменных окружения
load_dotenv()

TELEGRAM_API_TOKEN = os.getenv('TELEGRAM_API_TOKEN')
OPENAI_API_KEY = os.getenv('OPENAI_API_KEY')
GOOGLE_SERVICE_ACCOUNT_FILE = os.getenv('GOOGLE_SERVICE_ACCOUNT_FILE')
SOURCE_SPREADSHEET_ID = os.getenv('SOURCE_SPREADSHEET_ID')
ALLOWED_ADMIN_IDS = os.getenv('ALLOWED_ADMIN_IDS', '').split(',')

logger.debug("Используемый интерпретатор Python: %s", sys.executable)
logger.debug("Пути поиска модулей: %s", sys.path)

# Инициализация бота и диспетчера
bot = Bot(token=TELEGRAM_API_TOKEN, parse_mode='HTML', timeout=60)
storage = MemoryStorage()
dp = Dispatcher(bot, storage=storage)

# Инициализация OpenAI
openai.api_key = OPENAI_API_KEY


# Определение клавиатур
def get_main_keyboard():
    keyboard = InlineKeyboardMarkup(row_width=2)
    keyboard.add(
        InlineKeyboardButton("Создать счет-фактуру", callback_data="create_table"),
        InlineKeyboardButton("Регистрация клиента", callback_data="register_client"),
        InlineKeyboardButton("Поиск клиента", callback_data="search_client"),
        InlineKeyboardButton("Мои счет-фактуры", callback_data="my_invoices"),
        InlineKeyboardButton("Счет-фактура в PDF", callback_data="sheet_to_pdf")
    )
    return keyboard


def get_cancel_keyboard():
    keyboard = InlineKeyboardMarkup()
    cancel_button = InlineKeyboardButton("Отмена", callback_data="cancel")
    keyboard.add(cancel_button)
    return keyboard


# Определение состояний
class RegistrationStates(StatesGroup):
    first_name = State()
    last_name = State()
    phone_number = State()
    city = State()


class CreateTableStates(StatesGroup):
    client_id = State()
    manager_name = State()


class SearchStates(StatesGroup):
    waiting_for_phone_number = State()


class LinkPDFStates(StatesGroup):
    waiting_for_link = State()


# Инициализация клиента Google Sheets и Drive API
def get_gspread_client():
    scopes = [
        'https://www.googleapis.com/auth/spreadsheets',
        'https://www.googleapis.com/auth/drive'
    ]
    creds = Credentials.from_service_account_file(
        GOOGLE_SERVICE_ACCOUNT_FILE,
        scopes=scopes
    )
    client = gspread.authorize(creds)
    return client, creds


gspread_client, creds = get_gspread_client()

drive_service = build('drive', 'v3', credentials=creds)


# Инициализация базы данных
async def initialize_db():
    async with aiosqlite.connect('bot_data.db') as conn:
        await conn.execute('''
            CREATE TABLE IF NOT EXISTS clients (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                first_name TEXT NOT NULL,
                last_name TEXT NOT NULL,
                phone_number TEXT NOT NULL,
                last_money TEXT,
                last_sf_id TEXT,
                city TEXT NOT NULL
            )
        ''')
        await conn.execute('''
            CREATE TABLE IF NOT EXISTS user_questions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                chat_id INTEGER NOT NULL,
                question TEXT NOT NULL,
                date TEXT NOT NULL,
                time TEXT NOT NULL
            )
        ''')
        await conn.execute('''
            CREATE TABLE IF NOT EXISTS opened_sf (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                sf_id TEXT NOT NULL,
                chat_id INTEGER NOT NULL,
                first_name TEXT NOT NULL,
                last_name TEXT NOT NULL,
                city TEXT NOT NULL,
                manager_name TEXT NOT NULL,
                client_id TEXT NOT NULL,
                phone_number TEXT NOT NULL,
                date_opened TEXT NOT NULL,
                last_opened TEXT
            )
        ''')
        await conn.commit()
    logger.info("База данных инициализирована.")


# Вспомогательные функции для работы с базой данных
async def get_client_data_async(client_id: int):
    async with aiosqlite.connect('bot_data.db') as conn:
        cursor = await conn.execute('SELECT first_name, last_name, phone_number, city FROM clients WHERE id = ?',
                                    (client_id,))
        result = await cursor.fetchone()
        return result


async def update_last_money_async(client_id: int, last_money: str):
    async with aiosqlite.connect('bot_data.db') as conn:
        await conn.execute('UPDATE clients SET last_money = ? WHERE id = ?', (last_money, client_id))
        await conn.commit()


async def update_last_sf_id_async(client_id: int, sf_id: str):
    async with aiosqlite.connect('bot_data.db') as conn:
        await conn.execute('UPDATE clients SET last_sf_id = ? WHERE id = ?', (sf_id, client_id))
        await conn.commit()


async def save_opened_sf_async(sf_id: str, chat_id: int, first_name: str, last_name: str, city: str, manager_name: str,
                               client_id: int, phone_number: str):
    date_opened = datetime.now().strftime('%d.%m.%Y %H:%M:%S')
    async with aiosqlite.connect('bot_data.db') as conn:
        await conn.execute('''
            INSERT INTO opened_sf (sf_id, chat_id, first_name, last_name, city, manager_name, client_id, phone_number, date_opened, last_opened)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ''', (
        sf_id, chat_id, first_name, last_name, city, manager_name, client_id, phone_number, date_opened, date_opened))
        await conn.commit()


async def get_invoices_by_chat_id_async(chat_id: int):
    async with aiosqlite.connect('bot_data.db') as conn:
        cursor = await conn.execute('SELECT sf_id FROM opened_sf WHERE chat_id = ?', (chat_id,))
        results = await cursor.fetchall()
        return [row[0] for row in results]


async def get_all_questions_async():
    async with aiosqlite.connect('bot_data.db') as conn:
        cursor = await conn.execute('SELECT chat_id, question, date, time FROM user_questions')
        return await cursor.fetchall()


async def get_all_clients_async():
    async with aiosqlite.connect('bot_data.db') as conn:
        cursor = await conn.execute(
            'SELECT id, first_name, last_name, phone_number, last_money, last_sf_id, city FROM clients')
        return await cursor.fetchall()


async def save_question_to_db_async(chat_id: int, question: str):
    now = datetime.now()
    date = now.strftime('%d.%m.%Y')
    time_str = now.strftime('%H:%M:%S')
    async with aiosqlite.connect('bot_data.db') as conn:
        await conn.execute('''
            INSERT INTO user_questions (chat_id, question, date, time)
            VALUES (?, ?, ?, ?)
        ''', (chat_id, question, date, time_str))
        await conn.commit()


# Вспомогательные функции для работы с OpenAI
async def get_openai_response(question: str) -> str:
    try:
        response = await openai.ChatCompletion.acreate(
            model="gpt-3.5-turbo",
            messages=[
                {"role": "system", "content": "Ты помощник Telegram бота."},
                {"role": "user", "content": question}
            ],
            max_tokens=150
        )
        answer = response['choices'][0]['message']['content'].strip()
        return answer
    except Exception as e:
        logger.error(f"Ошибка при обращении к OpenAI: {e}", exc_info=True)
        return "Извините, произошла ошибка при обработке вашего запроса."


# Функция копирования таблицы
def create_spreadsheet_copy():
    try:
        copy_title = f"Счет-фактура - {datetime.now().strftime('%d.%m.%Y')}"
        copied_file = drive_service.files().copy(
            fileId=SOURCE_SPREADSHEET_ID,
            body={"name": copy_title}
        ).execute()

        new_spreadsheet_id = copied_file.get('id')
        if not new_spreadsheet_id:
            raise ValueError("Не удалось получить ID новой таблицы.")

        # Установка разрешений для конкретного пользователя
        permission = {
            'type': 'user',
            'role': 'writer',
            'emailAddress': 'golandecn@gmail.com'  # Замените на ваш email
        }
        drive_service.permissions().create(
            fileId=new_spreadsheet_id,
            body=permission,
            fields='id'
        ).execute()

        logger.info(f"Таблица скопирована с ID: {new_spreadsheet_id}")
        return new_spreadsheet_id
    except Exception as e:
        logger.error(f"Ошибка при копировании таблицы: {e}", exc_info=True)
        return None


# Функция переименования таблицы
def rename_spreadsheet(spreadsheet_id: str, first_name: str, last_name: str, manager_name: str) -> bool:
    try:
        today = datetime.today().strftime('%d.%m.%Y')
        new_title = f"{first_name} {last_name} - {manager_name} {today}"

        drive_service.files().update(
            fileId=spreadsheet_id,
            body={"name": new_title}
        ).execute()
        logger.info(f"Таблица {spreadsheet_id} переименована в {new_title}")
        return True
    except Exception as e:
        logger.error(f"Ошибка при переименовании таблицы {spreadsheet_id}: {e}", exc_info=True)
        return False


# Функция получения URL таблицы
def get_spreadsheet_url(spreadsheet_id: str) -> str:
    return f"https://docs.google.com/spreadsheets/d/{spreadsheet_id}/edit"


# Функция экспорта таблицы в PDF
async def export_spreadsheet_to_pdf(message: types.Message, sheet_url: str):
    try:
        spreadsheet_id = re.search(r'/d/([a-zA-Z0-9-_]+)', sheet_url).group(1)
        pdf_export_url = f"https://docs.google.com/spreadsheets/d/{spreadsheet_id}/export?format=pdf"

        response = drive_service.files().export(fileId=spreadsheet_id, mimeType='application/pdf').execute()
        pdf_data = BytesIO(response)
        pdf_data.seek(0)

        await message.reply_document(document=pdf_data, filename=f"Счет-фактура-{spreadsheet_id}.pdf",
                                     caption="Вот ваша счет-фактура в формате PDF.")
    except Exception as e:
        logger.error(f"Ошибка при экспорте таблицы в PDF: {e}", exc_info=True)
        await message.reply("Произошла ошибка при экспорте таблицы в PDF. Пожалуйста, попробуйте позже.",
                            reply_markup=get_main_keyboard())


# Вспомогательные функции для валидации
def is_valid_name(name: str) -> bool:
    return bool(re.match(r'^[A-Za-zА-Яа-яЁё\s-]+$', name))


def is_valid_phone_number(phone_number: str) -> bool:
    pattern = re.compile(r'^\+?\d{10,15}$')
    return bool(pattern.match(phone_number))


def is_valid_city(city: str) -> bool:
    ALLOWED_CITIES = ["Москва", "Санкт-Петербург", "Новосибирск", "Екатеринбург", "Казань", "Нижний Новгород",
                      "Челябинск", "Самара", "Омск", "Ростов-на-Дону"]
    return city in ALLOWED_CITIES


# Функция удаления предыдущих сообщений бота
async def delete_last_bot_message(state: FSMContext, message: types.Message):
    data = await state.get_data()
    last_bot_message_id = data.get('last_bot_message_id')
    if last_bot_message_id:
        try:
            await bot.delete_message(message.chat.id, last_bot_message_id)
        except Exception as e:
            logger.error(f"Ошибка при удалении сообщения: {e}", exc_info=True)


# Обработчики команд и состояний

# Обработчик команды /start
@dp.message_handler(commands=['start'])
async def send_welcome(message: types.Message):
    await message.reply("Добро пожаловать! Выберите действие:", reply_markup=get_main_keyboard())


# Обработчик отмены процесса
@dp.callback_query_handler(lambda c: c.data == 'cancel', state='*')
async def cancel_process(callback_query: types.CallbackQuery, state: FSMContext):
    await delete_last_bot_message(state, callback_query.message)
    await state.finish()
    await bot.answer_callback_query(callback_query.id, text="Процесс отменен.")
    await bot.send_message(callback_query.from_user.id, "Вы отменили процесс.", reply_markup=get_main_keyboard())


# Обработчик создания таблицы (SF)
@dp.callback_query_handler(lambda c: c.data == 'create_table')
async def process_callback_create_table(callback_query: types.CallbackQuery, state: FSMContext):
    await CreateTableStates.client_id.set()
    await bot.send_message(callback_query.from_user.id,
                           "Пожалуйста, введите ID клиента для которого необходимо создать таблицу:",
                           reply_markup=get_cancel_keyboard())
    await state.update_data(last_bot_message_id=callback_query.message.message_id)


# Обработка ID клиента при создании таблицы
@dp.message_handler(state=CreateTableStates.client_id)
async def process_client_id(message: types.Message, state: FSMContext):
    client_id_text = message.text.strip()
    if not client_id_text.isdigit():
        await message.reply("ID клиента должен состоять только из цифр. Пожалуйста, попробуйте снова.",
                            reply_markup=get_cancel_keyboard())
        return
    client_id = int(client_id_text)
    client_data = await get_client_data_async(client_id)
    if not client_data:
        await message.reply("Клиент с таким ID не найден. Пожалуйста, попробуйте снова.",
                            reply_markup=get_cancel_keyboard())
        return
    async with state.proxy() as data:
        data['client_id'] = client_id
        data['first_name'], data['last_name'], data['phone_number'], data['city'] = client_data
    await CreateTableStates.next()
    bot_message = await message.reply("Пожалуйста, введите имя менеджера.", reply_markup=get_cancel_keyboard())
    await state.update_data(last_bot_message_id=bot_message.message_id)


# Обработка имени менеджера и создание таблицы
@dp.message_handler(state=CreateTableStates.manager_name)
async def process_manager_name_table(message: types.Message, state: FSMContext):
    await delete_last_bot_message(state, message)
    manager_name = message.text.strip()
    async with state.proxy() as data:
        client_id = data['client_id']
        first_name = data['first_name']
        last_name = data['last_name']
        city = data['city']
        phone_number = data['phone_number']

    # Создание SF
    spreadsheet_url = await create_sf(client_id, manager_name, message.chat.id)
    if not spreadsheet_url:
        await message.reply("Произошла ошибка при создании таблицы. Пожалуйста, попробуйте позже.",
                            reply_markup=get_main_keyboard())
        await state.finish()
        return

    await message.reply(f"Таблица создана и доступна по ссылке: {spreadsheet_url}", reply_markup=get_main_keyboard())
    await state.finish()


# Функция создания SF
async def create_sf(client_id: int, manager_name: str, chat_id: int):
    # Получение данных клиента
    client_data = await get_client_data_async(client_id)
    if not client_data:
        logger.error(f"Клиент с ID {client_id} не найден.")
        return None

    first_name, last_name, phone_number, city = client_data

    # Создание копии таблицы
    new_spreadsheet_id = await asyncio.to_thread(create_spreadsheet_copy)
    if not new_spreadsheet_id:
        logger.error("Не удалось создать копию таблицы.")
        return None

    # Переименование таблицы
    renamed = await asyncio.to_thread(rename_spreadsheet, new_spreadsheet_id, first_name, last_name, manager_name)
    if not renamed:
        logger.error("Не удалось переименовать таблицу.")
        return None

    # Получение значения last_money из ячейки K11 и обновление базы данных
    try:
        sheet = await asyncio.to_thread(gspread_client.open_by_key, new_spreadsheet_id)
        sheet = sheet.sheet1
        last_money = await asyncio.to_thread(lambda: sheet.acell('K11').value)
        await update_last_money_async(client_id, last_money)
    except Exception as e:
        logger.error(f"Ошибка при обновлении last_money для клиента {client_id}: {e}", exc_info=True)

    # Сохранение открытой SF
    await save_opened_sf_async(new_spreadsheet_id, chat_id, first_name, last_name, city, manager_name, client_id,
                               phone_number)

    # Обновление last_sf_id в таблице клиентов
    await update_last_sf_id_async(client_id, new_spreadsheet_id)

    # Получение URL таблицы
    spreadsheet_url = get_spreadsheet_url(new_spreadsheet_id)

    return spreadsheet_url


# Обработчик поиска клиента
@dp.callback_query_handler(lambda c: c.data == 'search_client')
async def search_client_start(callback_query: types.CallbackQuery, state: FSMContext):
    await SearchStates.waiting_for_phone_number.set()
    await bot.send_message(callback_query.from_user.id,
                           "Введите номер телефона клиента для поиска в формате +7xxxxxxxxxx или +99x-xxx-xxx-xxx:",
                           reply_markup=get_cancel_keyboard())
    await state.update_data(last_bot_message_id=callback_query.message.message_id)


@dp.message_handler(state=SearchStates.waiting_for_phone_number)
async def process_phone_number_search(message: types.Message, state: FSMContext):
    phone_number = message.text.strip()
    if not is_valid_phone_number(phone_number):
        await message.reply(
            "Неверный формат номера телефона. Пожалуйста, используйте формат +7xxxxxxxxxx или аналогичный.",
            reply_markup=get_cancel_keyboard())
        return
    client = await get_client_by_phone_async(phone_number)
    if client:
        client_id = client[0]
        await message.reply(f"ID клиента с номером телефона {phone_number}: {client_id}",
                            reply_markup=get_main_keyboard())
    else:
        await message.reply("Клиент с таким номером не найден", reply_markup=get_main_keyboard())
    await state.finish()


# Обработчик "Мои счет-фактуры"
@dp.callback_query_handler(lambda c: c.data == 'my_invoices')
async def process_my_invoices(callback_query: types.CallbackQuery):
    chat_id = callback_query.from_user.id
    invoices = await get_invoices_by_chat_id_async(chat_id)

    if invoices:
        response = "Ваши последние счет-фактуры:\n"
        for invoice in invoices:
            response += f"https://docs.google.com/spreadsheets/d/{invoice}/edit\n"
    else:
        response = "У вас нет доступных счет-фактур."

    await bot.send_message(chat_id, response, reply_markup=get_main_keyboard())


# Обработчик "Счет-фактура в PDF"
@dp.callback_query_handler(lambda c: c.data == 'sheet_to_pdf')
async def send_pdf(callback_query: types.CallbackQuery, state: FSMContext):
    await LinkPDFStates.waiting_for_link.set()
    await bot.send_message(callback_query.from_user.id,
                           "Отправьте ссылку на Google Таблицу, чтобы получить её в формате PDF.",
                           reply_markup=get_cancel_keyboard())
    await state.update_data(last_bot_message_id=callback_query.message.message_id)


@dp.message_handler(state=LinkPDFStates.waiting_for_link, content_types=types.ContentTypes.TEXT)
async def handle_sheet_to_pdf(message: types.Message, state: FSMContext):
    sheet_url = message.text.strip()
    if 'docs.google.com/spreadsheets' not in sheet_url:
        await message.answer("Пожалуйста, отправьте корректную ссылку на Google Таблицу.",
                             reply_markup=get_cancel_keyboard())
        return
    await export_spreadsheet_to_pdf(message, sheet_url)
    await state.finish()


# Обработчик команды /allquestions
@dp.message_handler(commands=['allquestions'])
async def send_all_questions(message: types.Message):
    if str(message.chat.id) not in ALLOWED_ADMIN_IDS:
        await message.reply("У вас нет прав на выполнение этой команды.")
        return

    questions = await get_all_questions_async()
    if not questions:
        await message.reply("Вопросы не найдены.")
        return

    response = "Все вопросы:\n\n"
    for chat_id, question, date, time in questions:
        response += f"Chat ID: {chat_id}\nВопрос: {question}\nДата: {date}\nВремя: {time}\n\n"

    # Telegram ограничивает длину сообщения до 4096 символов
    if len(response) > 4000:
        messages = [response[i:i + 4000] for i in range(0, len(response), 4000)]
        for msg in messages:
            await message.reply(msg)
    else:
        await message.reply(response)


# Обработчик команды /getalldataclients
@dp.message_handler(commands=['getalldataclients'])
async def get_all_data_clients_handler(message: types.Message):
    if str(message.chat.id) not in ALLOWED_ADMIN_IDS:
        await message.reply("У вас нет прав на выполнение этой команды.")
        return

    clients = await get_all_clients_async()
    if not clients:
        await message.reply("База данных клиентов пуста.")
        return

    response = "Данные всех клиентов:\n\n"
    for client in clients:
        response += (f"ID: {client[0]}\n"
                     f"Имя: {client[1]}\n"
                     f"Фамилия: {client[2]}\n"
                     f"Телефон: {client[3]}\n"
                     f"Баланс: {client[4]}\n"
                     f"ID последней SF: {client[5]}\n"
                     f"Город: {client[6]}\n\n")

    if len(response) > 4000:
        messages = [response[i:i + 4000] for i in range(0, len(response), 4000)]
        for msg in messages:
            await message.reply(msg)
    else:
        await message.reply(response)


# Обработчик пользовательских сообщений
@dp.message_handler()
async def handle_user_message(message: types.Message):
    user_question = message.text.strip().lower()
    await save_question_to_db_async(message.chat.id, user_question)
    response = await get_openai_response(user_question)
    await message.reply(response)


# Функция получения клиента по телефону
async def get_client_by_phone_async(phone_number: str):
    async with aiosqlite.connect('bot_data.db') as conn:
        cursor = await conn.execute('SELECT id FROM clients WHERE phone_number = ?', (phone_number,))
        result = await cursor.fetchone()
        return result


# Функция регистрации клиента
@dp.callback_query_handler(lambda c: c.data == 'register_client')
async def register_client_start(callback_query: types.CallbackQuery, state: FSMContext):
    await RegistrationStates.first_name.set()
    await bot.send_message(callback_query.from_user.id, "Введите имя клиента:", reply_markup=get_cancel_keyboard())
    await state.update_data(last_bot_message_id=callback_query.message.message_id)


@dp.message_handler(state=RegistrationStates.first_name)
async def process_first_name(message: types.Message, state: FSMContext):
    first_name = message.text.strip()
    if not is_valid_name(first_name):
        await message.reply("Имя содержит недопустимые символы. Пожалуйста, попробуйте снова.",
                            reply_markup=get_cancel_keyboard())
        return
    await state.update_data(first_name=first_name)
    await RegistrationStates.next()
    bot_message = await message.reply("Введите фамилию клиента:", reply_markup=get_cancel_keyboard())
    await state.update_data(last_bot_message_id=bot_message.message_id)


@dp.message_handler(state=RegistrationStates.last_name)
async def process_last_name(message: types.Message, state: FSMContext):
    last_name = message.text.strip()
    if not is_valid_name(last_name):
        await message.reply("Фамилия содержит недопустимые символы. Пожалуйста, попробуйте снова.",
                            reply_markup=get_cancel_keyboard())
        return
    await state.update_data(last_name=last_name)
    await RegistrationStates.next()
    bot_message = await message.reply("Введите номер телефона клиента (формат +7xxxxxxxxxx):",
                                      reply_markup=get_cancel_keyboard())
    await state.update_data(last_bot_message_id=bot_message.message_id)


@dp.message_handler(state=RegistrationStates.phone_number)
async def process_phone_number(message: types.Message, state: FSMContext):
    phone_number = message.text.strip()
    if not is_valid_phone_number(phone_number):
        await message.reply(
            "Неверный формат номера телефона. Пожалуйста, используйте формат +7xxxxxxxxxx или аналогичный.",
            reply_markup=get_cancel_keyboard())
        return
    await state.update_data(phone_number=phone_number)
    await RegistrationStates.next()
    bot_message = await message.reply("Введите город клиента:", reply_markup=get_cancel_keyboard())
    await state.update_data(last_bot_message_id=bot_message.message_id)


@dp.message_handler(state=RegistrationStates.city)
async def process_city(message: types.Message, state: FSMContext):
    city = message.text.strip()
    if not is_valid_city(city):
        await message.reply("Город не распознан или не поддерживается. Пожалуйста, выберите из списка доступных.",
                            reply_markup=get_cancel_keyboard())
        return
    async with state.proxy() as data:
        data['city'] = city
        first_name = data['first_name']
        last_name = data['last_name']
        phone_number = data['phone_number']

    # Сохранение клиента в базу данных
    await save_client_async(first_name, last_name, phone_number, city)
    await delete_last_bot_message(state, message)
    await message.reply("Клиент успешно зарегистрирован.", reply_markup=get_main_keyboard())
    await state.finish()


# Функция сохранения клиента
async def save_client_async(first_name: str, last_name: str, phone_number: str, city: str):
    async with aiosqlite.connect('bot_data.db') as conn:
        await conn.execute('''
            INSERT INTO clients (first_name, last_name, phone_number, city)
            VALUES (?, ?, ?, ?)
        ''', (first_name, last_name, phone_number, city))
        await conn.commit()
    logger.info(f"Новый клиент зарегистрирован: {first_name} {last_name}, Телефон: {phone_number}, Город: {city}")


# Функция получения ответа от OpenAI уже определена выше

# Функция регистрации обработчиков завершена выше

# Функция запуска бота
async def on_startup(dp: Dispatcher):
    logger.info("Бот запускается...")
    await initialize_db()


async def on_shutdown(dp: Dispatcher):
    await bot.close()
    await dp.storage.close()
    await dp.storage.wait_closed()
    logger.info("Бот остановлен.")


# Основной запуск
if __name__ == '__main__':
    executor.start_polling(dp, skip_updates=True, on_startup=on_startup, on_shutdown=on_shutdown)
