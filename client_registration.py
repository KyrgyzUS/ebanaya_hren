# client_registration.py

from aiogram import Bot, types
from aiogram.dispatcher import FSMContext
from aiogram.dispatcher.filters.state import State, StatesGroup
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton, CallbackQuery

from databases import (
    save_client_data,
    get_client_by_phone,
    get_all_clients
)

from aiogram import Dispatcher

# Клавиатура для основных действий
keyboard = InlineKeyboardMarkup(row_width=2)
create_button = InlineKeyboardButton("Создать счет-фактуру", callback_data="create_table")
register_button = InlineKeyboardButton("Регистрация клиента", callback_data="register_client")
search_button = InlineKeyboardButton("Поиск клиента", callback_data="search_client")
invoices_button = InlineKeyboardButton("Мои счет-фактуры", callback_data="my_invoices")
PDF_button = InlineKeyboardButton("Счет-фактура в PDF", callback_data="sheet_to_pdf")
keyboard.add(create_button, register_button, search_button, invoices_button, PDF_button)

# Состояния для машины состояний регистрации клиента
class RegistrationStates(StatesGroup):
    first_name = State()
    last_name = State()
    phone_number = State()
    city = State()

# Функция для создания клавиатуры "Отмена"
def cancel_keyboard():
    keyboard = types.InlineKeyboardMarkup()
    cancel_button = types.InlineKeyboardButton("Отмена", callback_data="cancel")
    keyboard.add(cancel_button)
    return keyboard

# Функция для удаления последнего сообщения бота
async def delete_last_bot_message(state: FSMContext, message: types.Message):
    data = await state.get_data()
    last_bot_message_id = data.get('last_bot_message_id')
    if last_bot_message_id:
        try:
            await message.bot.delete_message(message.chat.id, last_bot_message_id)
        except Exception as e:
            print(f"Ошибка при удалении сообщения: {e}")

# Функции для обработки регистрации клиента
async def start_registration(callback_query: types.CallbackQuery, state: FSMContext, bot: Bot):
    await RegistrationStates.first_name.set()
    sent_message = await bot.send_message(
        callback_query.from_user.id,
        "Введите имя клиента:",
        reply_markup=cancel_keyboard()
    )
    await state.update_data(last_bot_message_id=sent_message.message_id)

async def process_first_name_registration(message: types.Message, state: FSMContext):
    await delete_last_bot_message(state, message)
    async with state.proxy() as data:
        data['first_name'] = message.text
    await RegistrationStates.next()
    sent_message = await message.reply("Введите фамилию клиента:", reply_markup=cancel_keyboard())
    await state.update_data(last_bot_message_id=sent_message.message_id)

async def process_last_name_registration(message: types.Message, state: FSMContext):
    await delete_last_bot_message(state, message)
    async with state.proxy() as data:
        data['last_name'] = message.text
    await RegistrationStates.next()
    sent_message = await message.reply("Введите номер телефона клиента:", reply_markup=cancel_keyboard())
    await state.update_data(last_bot_message_id=sent_message.message_id)

async def process_phone_number_registration(message: types.Message, state: FSMContext):
    await delete_last_bot_message(state, message)
    phone_number = message.text
    async with state.proxy() as data:
        data['phone_number'] = phone_number

    # Проверяем, существует ли клиент уже
    client = get_client_by_phone(phone_number)
    if client:
        await message.reply(f"Клиент уже зарегистрирован под ID: {client['id']}", reply_markup=keyboard)
        await state.finish()  # Завершаем процесс регистрации
        return

    await RegistrationStates.next()
    sent_message = await message.reply("Введите город клиента:", reply_markup=cancel_keyboard())
    await state.update_data(last_bot_message_id=sent_message.message_id)

async def process_city_registration(message: types.Message, state: FSMContext):
    await delete_last_bot_message(state, message)
    async with state.proxy() as data:
        data['city'] = message.text

    client_id = save_client_data(data)  # Синхронная функция
    if client_id:
        await message.reply(f"Регистрация завершена. Уникальный ID клиента: {client_id}", reply_markup=keyboard)
    else:
        await message.reply("Произошла ошибка при регистрации клиента. Пожалуйста, попробуйте снова.", reply_markup=keyboard)
    await state.finish()

# Хендлер для команды /getalldataclients
async def get_all_data_clients_handler(message: types.Message):
    allowed_chat_id = 5851877822
    if message.chat.id != allowed_chat_id:
        await message.reply("У вас нет прав на выполнение этой команды.")
        return

    clients = get_all_clients()

    if not clients:
        await message.reply("База данных клиентов пуста.")
        return

    response = "Данные всех клиентов:\n\n"
    for client in clients:
        response += (
            f"ID: {client['id']}\n"
            f"Имя: {client['first_name']}\n"
            f"Фамилия: {client['last_name']}\n"
            f"Телефон: {client['phone_number']}\n"
            f"Баланс: {client['last_money']}\n"
            f"ID последней SF: {client['last_sf_id']}\n"
            f"Город: {client['city']}\n\n"
        )

    # Если сообщение слишком длинное, разбиваем его на части
    if len(response) > 4000:
        messages = [response[i:i + 4000] for i in range(0, len(response), 4000)]
        for msg in messages:
            await message.reply(msg)
    else:
        await message.reply(response)

# Регистрация хендлеров в диспетчере
def register_handlers(dp: Dispatcher):
    dp.register_callback_query_handler(
        start_registration,
        lambda c: c.data == 'register_client',
        state='*'
    )
    dp.register_message_handler(
        process_first_name_registration,
        state=RegistrationStates.first_name
    )
    dp.register_message_handler(
        process_last_name_registration,
        state=RegistrationStates.last_name
    )
    dp.register_message_handler(
        process_phone_number_registration,
        state=RegistrationStates.phone_number
    )
    dp.register_message_handler(
        process_city_registration,
        state=RegistrationStates.city
    )
    dp.register_message_handler(
        get_all_data_clients_handler,
        commands=['getalldataclients']
    )
    # Добавьте хендлеры для отмены регистрации, если необходимо
