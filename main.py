# main.py

from dotenv import load_dotenv
load_dotenv()

import os
import asyncio
from aiogram import Bot, Dispatcher, types
from aiogram.utils import executor
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton, CallbackQuery
from aiogram.dispatcher import FSMContext
from aiogram.dispatcher.filters.state import State, StatesGroup
from aiogram.contrib.fsm_storage.memory import MemoryStorage
from openai import AsyncOpenAI, OpenAIError
import aiohttp
from client_registration import (
    RegistrationStates,
    start_registration,
    process_first_name_registration,
    process_last_name_registration,
    process_phone_number_registration,
    process_city_registration,
)
from client_registration import register_handlers as register_client_handlers
from databases import (
    get_client_by_phone,
    clean_phone_number,
    save_opened_sf,
    get_invoices_by_chat_id,
    fetch_and_update_last_money,
    initialize_db,
    save_question_to_db,
    get_all_questions,
    get_client_data,
    update_last_sf_id,
    update_value_periodically
)
from table_management import (
    create_spreadsheet_copy,
    rename_spreadsheet,
    delete_spreadsheet,
    get_spreadsheet_url,
    get_last_money,
    write_last_money_to_new_sf,
    export_spreadsheet_to_pdf
)
from knowledge_base import knowledge_base

# Инициализация баз данных
initialize_db()

# Получение переменных окружения
TELEGRAM_API_TOKEN = os.getenv('TELEGRAM_API_TOKEN')
OPENAI_API_KEY = os.getenv('OPENAI_API_KEY')
SERVICE_ACCOUNT_JSON_BASE64 = os.getenv('SERVICE_ACCOUNT_JSON_BASE64')  # Убедитесь, что это не используется, если вы используете SERVICE_ACCOUNT_JSON_BASE64

if not TELEGRAM_API_TOKEN:
    raise EnvironmentError("TELEGRAM_API_TOKEN не установлена в переменных окружения.")
if not OPENAI_API_KEY:
    raise EnvironmentError("OPENAI_API_KEY не установлена в переменных окружения.")

# Инициализация бота и диспетчера
bot = Bot(token=TELEGRAM_API_TOKEN, timeout=60)
dp = Dispatcher(bot, storage=MemoryStorage())

# Инициализация клиента OpenAI
client = AsyncOpenAI(api_key=OPENAI_API_KEY)

# Определение состояний для создания таблицы
class CreateTableStates(StatesGroup):
    client_id = State()
    manager_name = State()

# Определение состояний для поиска клиента
class SearchStates(StatesGroup):
    waiting_for_phone_number = State()

# Определение состояний для связывания PDF
class LinkPDFStates(StatesGroup):
    waiting_for_Link = State()

# Основная клавиатура
keyboard = InlineKeyboardMarkup(row_width=2)
create_button = InlineKeyboardButton("Создать счет-фактуру", callback_data="create_table")
register_button = InlineKeyboardButton("Регистрация клиента", callback_data="register_client")
search_button = InlineKeyboardButton("Поиск клиента", callback_data="search_client")
invoices_button = InlineKeyboardButton("Мои счет-фактуры", callback_data="my_invoices")
PDF_button = InlineKeyboardButton("Счет-фактура в PDF", callback_data="sheet_to_pdf")
keyboard.add(create_button, register_button, search_button, invoices_button, PDF_button)

# Глобальные переменные для хранения данных пользователя
user_data = {}

# Регистрация хендлеров из client_registration.py
register_client_handlers(dp)

# Хендлеры для регистрации клиента
@dp.callback_query_handler(lambda c: c.data == 'register_client')
async def start_registration_handler(callback_query: types.CallbackQuery, state: FSMContext):
    await start_registration(callback_query, state, bot)

@dp.message_handler(state=RegistrationStates.first_name)
async def process_first_name_handler(message: types.Message, state: FSMContext):
    await process_first_name_registration(message, state)

@dp.message_handler(state=RegistrationStates.last_name)
async def process_last_name_handler(message: types.Message, state: FSMContext):
    await process_last_name_registration(message, state)

@dp.message_handler(state=RegistrationStates.phone_number)
async def process_phone_number_handler(message: types.Message, state: FSMContext):
    await process_phone_number_registration(message, state)

@dp.message_handler(state=RegistrationStates.city)
async def process_city_handler(message: types.Message, state: FSMContext):
    await process_city_registration(message, state)

# Хендлеры для создания таблицы
@dp.callback_query_handler(lambda c: c.data == 'create_table')
async def process_callback_create_table(callback_query: types.CallbackQuery):
    user_id = callback_query.from_user.id
    await bot.send_message(user_id, "Пожалуйста, введите ID клиента для которого необходимо создать таблицу:", reply_markup=cancel_keyboard())
    await CreateTableStates.client_id.set()

@dp.message_handler(state=CreateTableStates.client_id)
async def process_client_id(message: types.Message, state: FSMContext):
    user_id = message.from_user.id
    client_id = message.text
    if not client_id.isdigit():
        await message.reply("ID клиента должно быть числом. Попробуйте снова.")
        return
    client_data = get_client_data(int(client_id))
    if not client_data:
        await message.reply("Клиент с таким ID не найден. Пожалуйста, попробуйте снова.", reply_markup=cancel_keyboard())
        return

    async with state.proxy() as data:
        # Сохраняем данные клиента по отдельным ключам
        data['client_id'] = client_id
        data['first_name'] = client_data['first_name']
        data['last_name'] = client_data['last_name']
        data['phone_number'] = client_data['phone_number']
        data['city'] = client_data['city']

    await CreateTableStates.next()
    sent_message = await message.reply("Пожалуйста, введите имя менеджера.", reply_markup=cancel_keyboard())
    await state.update_data(last_bot_message_id=sent_message.message_id)

@dp.message_handler(state=CreateTableStates.manager_name)
async def process_manager_name_table(message: types.Message, state: FSMContext):
    user_id = message.from_user.id
    async with state.proxy() as data:
        data['manager_name'] = message.text

        client_id = data['client_id']

        # Обновление last_money перед созданием новой счет-фактуры
        fetch_and_update_last_money(client_id)

        # Получение обновленного значения last_money из базы данных
        last_money = get_last_money(client_id)

        # Копирование исходной таблицы
        new_spreadsheet_id = create_spreadsheet_copy()
        if not new_spreadsheet_id:
            await message.reply("Произошла ошибка при создании таблицы. Пожалуйста, попробуйте позже.", reply_markup=keyboard)
            await state.finish()
            return

        user_data[user_id] = {'spreadsheet_id': new_spreadsheet_id}

        # Переименование файла
        first_name = data['first_name']
        last_name = data['last_name']
        city = data['city']
        manager_name = data['manager_name']
        rename_spreadsheet(new_spreadsheet_id, first_name, city, manager_name)

        # Запись обновленного last_money в новую таблицу
        if last_money:
            write_last_money_to_new_sf(new_spreadsheet_id, last_money)

        # Обновление last_sf_id для клиента
        update_last_sf_id(client_id, new_spreadsheet_id)

        new_spreadsheet_url = get_spreadsheet_url(new_spreadsheet_id)
        await message.reply(f"Таблица переименована и доступна для всех: {new_spreadsheet_url}", reply_markup=keyboard)

        # Сохранение открытой счет-фактуры в базе данных
        save_opened_sf(
            sf_id=new_spreadsheet_id,
            chat_id=user_id,
            first_name=first_name,
            last_name=last_name,
            city=city,
            manager_name=manager_name,
            client_id=client_id,
            phone_number=data['phone_number']
        )

    await state.finish()
    user_data.pop(user_id, None)

# Обработчик для кнопки "Мои счет-фактуры"
@dp.callback_query_handler(lambda c: c.data == 'my_invoices')
async def process_my_invoices(callback_query: CallbackQuery):
    chat_id = callback_query.from_user.id
    invoices = get_invoices_by_chat_id(chat_id)

    if invoices:
        response = "Ваши последние счет-фактуры:\n"
        for invoice in invoices:
            response += f"{invoice}\n"
    else:
        response = "У вас нет доступных счет-фактур."

    await bot.send_message(chat_id, response, reply_markup=keyboard)

# Функция для создания клавиатуры "Отмена"
def cancel_keyboard():
    keyboard = InlineKeyboardMarkup()
    cancel_button = InlineKeyboardButton("Отмена", callback_data="cancel")
    keyboard.add(cancel_button)
    return keyboard

# Обработчик для кнопки "Отмена"
@dp.callback_query_handler(lambda c: c.data == 'cancel', state='*')
async def cancel_process(callback_query: CallbackQuery, state: FSMContext):
    user_id = callback_query.from_user.id
    current_state = await state.get_state()

    if current_state is not None:
        await state.finish()

        # Проверяем, есть ли таблица для удаления
        spreadsheet_id = user_data.get(user_id, {}).get('spreadsheet_id')
        if spreadsheet_id:
            try:
                delete_spreadsheet(spreadsheet_id)
                await bot.send_message(user_id, "Процесс отменен и таблица удалена.", reply_markup=keyboard)
            except Exception as e:
                await bot.send_message(user_id, f"Не удалось удалить таблицу: {e}", reply_markup=keyboard)

        await bot.delete_message(callback_query.message.chat.id, callback_query.message.message_id)
        await bot.send_message(user_id, "Процесс отменен.", reply_markup=keyboard)

# Хендлер для команды /start
@dp.message_handler(commands=['start'])
async def send_welcome(message: types.Message):
    await message.reply(
        "Привет! Нажмите на кнопку ниже, чтобы создать таблицу или найти клиента. Или напишите мне, чтобы узнать о доставке или других моментах",
        reply_markup=keyboard
    )

# Хендлер для начала поиска клиента
@dp.callback_query_handler(lambda c: c.data == 'search_client')
async def search_client_start(callback_query: CallbackQuery):
    await SearchStates.waiting_for_phone_number.set()
    await bot.send_message(callback_query.from_user.id, "Введите номер телефона клиента для поиска в формате +7хххххххххх или +99х-ххх-ххх-ххх :", reply_markup=cancel_keyboard())

# Хендлер для обработки номера телефона при поиске клиента
@dp.message_handler(state=SearchStates.waiting_for_phone_number)
async def process_phone_number_search(message: types.Message, state: FSMContext):
    phone_number = message.text
    cleaned_phone_number = clean_phone_number(phone_number)  # Очистка номера телефона
    client = get_client_by_phone(cleaned_phone_number)

    if client:
        client_id = client['id']  # ID клиента находится в словаре
        await message.reply(f"ID клиента с номером телефона {phone_number}: {client_id}", reply_markup=keyboard)
    else:
        await message.reply("Клиент с таким номером не найден", reply_markup=keyboard)

    await state.finish()

# Хендлер для кнопки "Счет-фактура в PDF"
@dp.callback_query_handler(lambda c: c.data == 'sheet_to_pdf')
async def send_pdf(callback_query: CallbackQuery):
    await LinkPDFStates.waiting_for_Link.set()
    await bot.send_message(callback_query.from_user.id, "Отправьте ссылку на Google Таблицу, чтобы получить её в формате PDF.", reply_markup=cancel_keyboard())

@dp.message_handler(state=LinkPDFStates.waiting_for_Link)
async def handle_message(message: types.Message, state: FSMContext):
    if 'docs.google.com/spreadsheets' in message.text:
        await export_spreadsheet_to_pdf(message, message.text)
        await state.finish()  # Сбрасываем состояние после обработки
    else:
        await message.answer("Пожалуйста, отправьте корректную ссылку на Google Таблицу.", reply_markup=cancel_keyboard())

# Хендлер для команды /allquestions
@dp.message_handler(commands=['allquestions'])
async def send_all_questions(message: types.Message):
    questions = get_all_questions()
    if not questions:
        await message.reply("Вопросы не найдены.")
        return

    response = "Все вопросы:\n"
    for question in questions:
        response += (
            f"Chat ID: {question['chat_id']}\n"
            f"Вопрос: {question['question']}\n"
            f"Дата: {question['date']}\n"
            f"Время: {question['time']}\n\n"
        )

    # Если сообщение слишком длинное, разбиваем его на части
    if len(response) > 4000:
        messages = [response[i:i + 4000] for i in range(0, len(response), 4000)]
        for msg in messages:
            await message.reply(msg)
    else:
        await message.reply(response)

# Хендлер для всех остальных сообщений
@dp.message_handler()
async def handle_message(message: types.Message):
    user_question = message.text.lower()
    save_question_to_db(message.chat.id, user_question)  # Сохранение вопроса в базу данных
    response = await get_response(user_question)
    await message.reply(response)

# Функция для получения ответа от OpenAI
async def get_response(question):
    # Создание контекста на основе базы знаний
    knowledge_base_content = "\n".join([f"Ответ: {a}" for a in knowledge_base])
    messages = [
        {"role": "system", "content": "Ты ассистент. Используй следующую базу знаний для ответа на вопросы. Доставка только в города России, Казахстана, Узбекистана и Беларуси. Если у тебя спросят про доставку в город отсутствующий в базе, но в доступной стране, то скажи, что доставка в этот город осуществляется через ближайший город из базы и вышли информацию о доставке в него. Если ты не найдешь ответ в базе знаний, ответь: 'Проконсультируйтесь у Администратора +996 705 705 996'."},
        {"role": "user", "content": f"База знаний:\n{knowledge_base_content}\nВопрос: {question}"}
    ]
    return await get_openai_response(messages)

# Функция для взаимодействия с OpenAI API
async def get_openai_response(messages):
    try:
        response = await client.chat.completions.create(
            model="gpt-3.5-turbo",
            messages=messages
        )
        return response.choices[0].message.content.strip()
    except OpenAIError as e:
        if e.code == 'insufficient_quota':
            return "Произошла ошибка при запросе к OpenAI API: превышена квота использования. Пожалуйста, проверьте план и детали биллинга."
        return f"Произошла ошибка при i запросе к OpenAI API: {e}"

# Функция для создания клавиатуры "Отмена" (дублируется, можно удалить)
def cancel_keyboard():
    keyboard = InlineKeyboardMarkup()
    cancel_button = InlineKeyboardButton("Отмена", callback_data="cancel")
    keyboard.add(cancel_button)
    return keyboard

# Функция запуска при старте бота
async def on_startup(dp):
    print("Bot is starting up...")
    # Запуск периодического обновления в фоне
   # asyncio.create_task(update_value_periodically())

if __name__ == '__main__':
    executor.start_polling(dp, skip_updates=True, on_startup=on_startup)
