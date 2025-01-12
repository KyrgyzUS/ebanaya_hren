# table_management.py

import os
import json
import tempfile
import base64
from datetime import datetime
import asyncio
import pdfkit
from aiogram import types
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton, CallbackQuery

import gspread
from googleapiclient.discovery import build
from google.oauth2.service_account import Credentials

from databases import (
    get_db_connection,
    gspread_client,
    get_client_data,
    save_last_sf_id,
    get_last_money,
    write_last_money_to_new_sf
)

# Настройка клавиатуры
keyboard = InlineKeyboardMarkup(row_width=2)
create_button = InlineKeyboardButton("Создать счет-фактуру", callback_data="create_table")
register_button = InlineKeyboardButton("Регистрация клиента", callback_data="register_client")
search_button = InlineKeyboardButton("Поиск клиента", callback_data="search_client")
invoices_button = InlineKeyboardButton("Мои счет-фактуры", callback_data="my_invoices")
PDF_button = InlineKeyboardButton("Счет-фактура в PDF", callback_data="sheet_to_pdf")
keyboard.add(create_button, register_button, search_button, invoices_button, PDF_button)

# Настройки Google Drive API уже настроены в databases.py через gspread_client

# ID исходной таблицы
source_spreadsheet_id = '19vQzsbZPEzTzgMqGtqTCabSlzvdWhVNKIm8nWU2t47c'

def create_spreadsheet_copy():
    try:
        copy_title = "Copy of Source Spreadsheet"
        copied_file = gspread_client.drive_service.files().copy(
            fileId=source_spreadsheet_id,
            body={"name": copy_title}
        ).execute()

        new_spreadsheet_id = copied_file['id']

        # Сделать новую таблицу доступной для определенного пользователя
        permission = {
            'type': 'user',
            'role': 'writer',
            'emailAddress': 'golandecn@gmail.com'
        }
        gspread_client.drive_service.permissions().create(
            fileId=new_spreadsheet_id,
            body=permission,
            fields='id'
        ).execute()

        # Сделать таблицу доступной для всех с правами на запись
        permission_reader = {
            'type': 'anyone',
            'role': 'writer'
        }
        gspread_client.drive_service.permissions().create(
            fileId=new_spreadsheet_id,
            body=permission_reader,
        ).execute()

        return new_spreadsheet_id
    except Exception as e:
        print(f"Ошибка при создании копии таблицы: {e}")
        return None

def rename_spreadsheet(spreadsheet_id, first_name, last_name, manager_name):
    try:
        today = datetime.today().strftime('%d.%m.%Y')
        new_title = f"{first_name} {last_name} - {manager_name} {today}"

        gspread_client.drive_service.files().update(
            fileId=spreadsheet_id,
            body={"name": new_title}
        ).execute()
    except Exception as e:
        print(f"Ошибка при переименовании таблицы: {e}")

def delete_spreadsheet(spreadsheet_id):
    try:
        gspread_client.drive_service.files().delete(fileId=spreadsheet_id).execute()
    except Exception as e:
        print(f"Ошибка при удалении таблицы: {e}")

def save_last_sf_id(client_id, spreadsheet_id):
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute('''
        UPDATE clients
        SET last_sf_id = %s
        WHERE id = %s
        ''', (spreadsheet_id, client_id))
        conn.commit()
    except Exception as e:
        print(f"Ошибка при сохранении last_sf_id: {e}")
    finally:
        cursor.close()
        conn.close()

def get_spreadsheet_url(spreadsheet_id):
    return f"https://docs.google.com/spreadsheets/d/{spreadsheet_id}/edit"

def get_last_money(client_id):
    try:
        last_money = get_last_money(client_id)
        return last_money
    except Exception as e:
        print(f"Ошибка при получении last_money: {e}")
        return None

def write_last_money_to_new_sf(spreadsheet_id, last_money):
    try:
        sheet = gspread_client.open_by_key(spreadsheet_id).sheet1
        sheet.update_acell('G11', last_money)
    except Exception as e:
        print(f"Ошибка при записи last_money в SF: {e}")

# Функция для экспорта таблицы в PDF с использованием Google Drive API
async def export_spreadsheet_to_pdf(message: types.Message, sheet_url: str):
    try:
        # Извлекаем ID таблицы из URL
        SPREADSHEET_ID = extract_spreadsheet_id(sheet_url)
        if not SPREADSHEET_ID:
            await message.reply("Неверный URL таблицы.")
            return

        # Получаем название таблицы
        spreadsheet_title = get_spreadsheet_title(SPREADSHEET_ID)
        if not spreadsheet_title:
            spreadsheet_title = "Untitled Spreadsheet"

        # Экспортируем таблицу в PDF
        request = gspread_client.drive_service.files().export_media(fileId=SPREADSHEET_ID, mimeType='application/pdf')
        response = request.execute()

        # Создаем PDF файл с названием таблицы
        pdf_filename = f"{spreadsheet_title}.pdf"
        with open(pdf_filename, 'wb') as f:
            f.write(response)

        # Отправляем PDF файл пользователю
        with open(pdf_filename, 'rb') as pdf_file:
            await message.answer_document(pdf_file, reply_markup=keyboard)

        # Удаляем файл после отправки
        os.remove(pdf_filename)

    except Exception as e:
        print(f"Ошибка при экспорте таблицы в PDF: {e}")
        await message.reply("Произошла ошибка при экспорте таблицы в PDF.")

# Функция для извлечения ID таблицы из URL
def extract_spreadsheet_id(sheet_url: str) -> str:
    try:
        # Используем разбор URL для извлечения ID
        parts = sheet_url.split('/d/')
        if len(parts) < 2:
            return ''
        spreadsheet_id = parts[1].split('/')[0]
        return spreadsheet_id
    except Exception as e:
        print(f"Ошибка при извлечении ID таблицы: {e}")
        return ''

def get_spreadsheet_title(spreadsheet_id: str) -> str:
    try:
        file = gspread_client.drive_service.files().get(fileId=spreadsheet_id, fields='name').execute()
        spreadsheet_title = file.get('name', 'Untitled Spreadsheet')
        return spreadsheet_title
    except Exception as e:
        print(f"Ошибка при получении названия таблицы: {e}")
        return 'Untitled Spreadsheet'

# Функция для сохранения SF в базу данных
def save_opened_sf(sf_id, chat_id, first_name, last_name, city, manager_name, client_id, phone_number):
    try:
        from databases import save_opened_sf  # Импорт функции из databases.py
        save_opened_sf(sf_id, chat_id, first_name, last_name, city, manager_name, client_id, phone_number)
    except Exception as e:
        print(f"Ошибка при сохранении SF: {e}")

# Дополнительные функции и обработчики могут быть добавлены здесь
