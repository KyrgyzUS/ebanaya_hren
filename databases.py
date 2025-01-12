# databases.py

import os
import json
import tempfile
import base64
import psycopg2
from datetime import datetime
import psycopg2
from psycopg2.extras import RealDictCursor
import asyncio
import gspread
from googleapiclient.discovery import build
from google.oauth2.service_account import Credentials

# Настройки Google Sheets API
SCOPES = ['https://www.googleapis.com/auth/spreadsheets', 'https://www.googleapis.com/auth/drive']

# Получение и настройка учетных данных Google из Base64
SERVICE_ACCOUNT_JSON_BASE64 = os.getenv('SERVICE_ACCOUNT_JSON_BASE64')

if SERVICE_ACCOUNT_JSON_BASE64:
    try:
        service_account_json = base64.b64decode(SERVICE_ACCOUNT_JSON_BASE64).decode('utf-8')
    except Exception as e:
        raise EnvironmentError("Ошибка декодирования SERVICE_ACCOUNT_JSON_BASE64: " + str(e))

    with tempfile.NamedTemporaryFile(mode='w+', delete=False) as temp_file:
        temp_file.write(service_account_json)
        temp_file_path = temp_file.name

    try:
        creds = Credentials.from_service_account_file(temp_file_path, scopes=SCOPES)
        gspread_client = gspread.authorize(creds)
    except Exception as e:
        raise EnvironmentError("Ошибка авторизации Google Sheets: " + str(e))
    finally:
        os.remove(temp_file_path)  # Удаление временного файла
else:
    raise EnvironmentError("SERVICE_ACCOUNT_JSON_BASE64 не установлена в переменных окружения.")


drive_service = build('drive', 'v3', credentials=creds)
# Функция для подключения к базе данных PostgreSQL
def get_db_connection():
    DATABASE_URL = os.getenv('DATABASE_URL')
    if not DATABASE_URL:
        raise EnvironmentError("DATABASE_URL не установлена в переменных окружения.")
    try:
        conn = psycopg2.connect(DATABASE_URL, sslmode='require')
        return conn
    except Exception as e:
        raise ConnectionError(f"Не удалось подключиться к базе данных: {e}")


# Инициализация базы данных
def initialize_db():
    conn = get_db_connection()
    cursor = conn.cursor()
    try:
        cursor.execute('''
        CREATE TABLE IF NOT EXISTS clients (
            id BIGSERIAL PRIMARY KEY,
            first_name TEXT NOT NULL,
            last_name TEXT NOT NULL,
            phone_number TEXT NOT NULL UNIQUE,
            last_money TEXT,
            last_sf_id TEXT,
            city TEXT NOT NULL
        )
        ''')
        cursor.execute('''
        CREATE TABLE IF NOT EXISTS user_questions (
            id BIGSERIAL PRIMARY KEY,
            chat_id BIGINT NOT NULL,
            question TEXT NOT NULL,
            date TIMESTAMP NOT NULL,
            time TEXT NOT NULL
        )
        ''')
        cursor.execute('''
        CREATE TABLE IF NOT EXISTS opened_sf (
            id BIGSERIAL PRIMARY KEY,
            sf_id TEXT NOT NULL,
            chat_id BIGINT NOT NULL,
            first_name TEXT NOT NULL,
            last_name TEXT NOT NULL,
            city TEXT NOT NULL,
            manager_name TEXT NOT NULL,
            client_id BIGINT NOT NULL REFERENCES clients(id),
            phone_number TEXT NOT NULL,
            date_opened TIMESTAMP NOT NULL,
            last_opened TEXT
        )
        ''')
        conn.commit()
    except Exception as e:
        conn.rollback()
        print(f"Ошибка при инициализации базы данных: {e}")
    finally:
        cursor.close()
        conn.close()


# Функция для очистки номера телефона от символов +, -, и пробелов
def clean_phone_number(phone_number):
    return ''.join(filter(str.isdigit, phone_number))


# Функция для получения счет-фактур по chat_id
def get_invoices_by_chat_id(chat_id):
    conn = get_db_connection()
    cursor = conn.cursor()
    try:
        cursor.execute('''
        SELECT sf_id, first_name, last_name, city, manager_name, client_id, phone_number, date_opened 
        FROM opened_sf 
        WHERE chat_id = %s 
        ORDER BY date_opened DESC 
        LIMIT 15
        ''', (chat_id,))

        invoices = cursor.fetchall()

        # Преобразование результатов в список с полными данными
        invoice_details = [
            f"{invoice[1]} {invoice[3]} - {invoice[4]} {invoice[7]} \n https://docs.google.com/spreadsheets/d/{invoice[0]} \n"
            for invoice in invoices
        ]
        return invoice_details
    except Exception as e:
        print(f"Ошибка при получении счет-фактур: {e}")
        return []
    finally:
        cursor.close()
        conn.close()


# Функция для получения всех клиентов
def get_all_clients():
    conn = get_db_connection()
    cursor = conn.cursor(cursor_factory=RealDictCursor)
    try:
        cursor.execute('SELECT * FROM clients')
        rows = cursor.fetchall()
        return rows
    except Exception as e:
        print(f"Ошибка при получении клиентов: {e}")
        return []
    finally:
        cursor.close()
        conn.close()


# Сохранение вопроса пользователя в базу данных
def save_question_to_db(chat_id, question):
    conn = get_db_connection()
    cursor = conn.cursor()
    try:
        date = datetime.now().strftime('%Y-%m-%d')
        time = datetime.now().strftime('%H:%M:%S')
        cursor.execute('''
        INSERT INTO user_questions (chat_id, question, date, time)
        VALUES (%s, %s, %s, %s)
        ''', (chat_id, question, date, time))
        conn.commit()
    except Exception as e:
        conn.rollback()
        print(f"Ошибка при сохранении вопроса: {e}")
    finally:
        cursor.close()
        conn.close()


# Получение всех вопросов из базы данных
def get_all_questions():
    conn = get_db_connection()
    cursor = conn.cursor(cursor_factory=RealDictCursor)
    try:
        cursor.execute('SELECT chat_id, question, date, time FROM user_questions')
        rows = cursor.fetchall()
        return rows
    except Exception as e:
        print(f"Ошибка при получении вопросов: {e}")
        return []
    finally:
        cursor.close()
        conn.close()


# Сохранение данных клиента в базу данных
def save_client_data(data):
    cleaned_number = clean_phone_number(data['phone_number'])
    default_last_sf_id = "10qgTYnltMgProehHva5rfU8ynujI_zG89zU-XzDV4lQ"  # Значение по умолчанию
    conn = get_db_connection()
    cursor = conn.cursor()
    try:
        cursor.execute('''
        INSERT INTO clients (first_name, last_name, phone_number, city, last_sf_id)
        VALUES (%s, %s, %s, %s, %s)
        RETURNING id
        ''', (data['first_name'], data['last_name'], cleaned_number, data['city'], default_last_sf_id))
        conn.commit()
        client_id = cursor.fetchone()[0]
        return client_id
    except Exception as e:
        conn.rollback()
        print(f"Ошибка при сохранении данных клиента: {e}")
        return None
    finally:
        cursor.close()
        conn.close()


# Получение данных клиента по ID
def get_client_data(client_id):
    conn = get_db_connection()
    cursor = conn.cursor(cursor_factory=RealDictCursor)
    try:
        cursor.execute('SELECT first_name, last_name, phone_number, city FROM clients WHERE id = %s', (client_id,))
        client_data = cursor.fetchone()
        return client_data
    except Exception as e:
        print(f"Ошибка при получении данных клиента: {e}")
        return None
    finally:
        cursor.close()
        conn.close()


# Обновление последней открытой SFID для клиента
def update_last_sf_id(client_id, sf_id):
    conn = get_db_connection()
    cursor = conn.cursor()
    try:
        cursor.execute('''
        UPDATE clients
        SET last_sf_id = %s
        WHERE id = %s
        ''', (sf_id, client_id))
        conn.commit()
    except Exception as e:
        conn.rollback()
        print(f"Ошибка при обновлении last_sf_id: {e}")
    finally:
        cursor.close()
        conn.close()


# Обновление значения last_money
def update_last_money(client_id, value):
    conn = get_db_connection()
    cursor = conn.cursor()
    try:
        cursor.execute('''
        UPDATE clients
        SET last_money = %s
        WHERE id = %s
        ''', (value, client_id))
        conn.commit()
    except Exception as e:
        conn.rollback()
        print(f"Ошибка при обновлении last_money: {e}")
    finally:
        cursor.close()
        conn.close()


# Функция для получения клиента по номеру телефона
def get_client_by_phone(phone_number):
    cleaned_number = clean_phone_number(phone_number)
    conn = get_db_connection()
    cursor = conn.cursor(cursor_factory=RealDictCursor)
    try:
        cursor.execute('SELECT * FROM clients WHERE phone_number = %s', (cleaned_number,))
        client = cursor.fetchone()
        return client
    except Exception as e:
        print(f"Ошибка при поиске клиента по телефону: {e}")
        return None
    finally:
        cursor.close()
        conn.close()


# Функция для сохранения SF в базу данных
def save_opened_sf(sf_id, chat_id, first_name, last_name, city, manager_name, client_id, phone_number):
    conn = get_db_connection()
    cursor = conn.cursor()
    try:
        date_opened = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        cursor.execute('''
        INSERT INTO opened_sf (sf_id, chat_id, first_name, last_name, city, manager_name, client_id, phone_number, date_opened)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
        ''', (sf_id, chat_id, first_name, last_name, city, manager_name, client_id, phone_number, date_opened))
        conn.commit()
    except Exception as e:
        conn.rollback()
        print(f"Ошибка при сохранении SF: {e}")
    finally:
        cursor.close()
        conn.close()


# Функция для получения счет-фактуры из Google Sheets и обновления last_money
def fetch_and_update_last_money(client_id):
    conn = get_db_connection()
    cursor = conn.cursor()
    try:
        cursor.execute('SELECT last_sf_id FROM clients WHERE id = %s', (client_id,))
        row = cursor.fetchone()
        if row and row[0]:
            last_sf_id = row[0]
            try:
                # Открытие соответствующей таблицы клиента
                sheet = gspread_client.open_by_key(last_sf_id).sheet1

                # Получение значения из ячейки K11
                last_money = sheet.acell('K11').value

                # Обновление значения last_money в базе данных
                cursor.execute('UPDATE clients SET last_money = %s WHERE id = %s', (last_money, client_id))
                conn.commit()
                print(f"last_money для клиента {client_id} обновлено значением: {last_money}")
            except Exception as e:
                print(f"Ошибка при обновлении last_money для клиента {client_id}: {e}")
        else:
            print(f"Клиент с ID {client_id} не имеет последней SFID.")
    except Exception as e:
        print(f"Ошибка при запросе last_sf_id: {e}")
    finally:
        cursor.close()
        conn.close()


# Асинхронная функция для периодического обновления значений из Google Sheets
async def update_value_periodically():
    while True:
        try:
            conn = get_db_connection()
            cursor = conn.cursor(cursor_factory=RealDictCursor)

            # Получение всех клиентов с установленным last_sf_id
            cursor.execute('SELECT id, last_sf_id FROM clients WHERE last_sf_id IS NOT NULL')
            clients = cursor.fetchall()

            for client in clients:
                client_id = client['id']
                last_sf_id = client['last_sf_id']
                try:
                    # Открытие соответствующей таблицы клиента
                    sheet = gspread_client.open_by_key(last_sf_id).sheet1

                    # Получение значения из ячейки K11
                    last_money = sheet.acell('K11').value

                    # Обновление значения last_money в базе данных
                    cursor.execute('UPDATE clients SET last_money = %s WHERE id = %s', (last_money, client_id))
                    conn.commit()
                    print(f"last_money для клиента {client_id} обновлено значением: {last_money}")
                except Exception as e:
                    print(f"Ошибка при обработке таблицы {last_sf_id} для клиента {client_id}: {e}")

            cursor.close()
            conn.close()

        except Exception as e:
            print(f"Ошибка в периодическом обновлении: {e}")

        # Ждать 1 час перед следующим обновлением
        await asyncio.sleep(3600)
