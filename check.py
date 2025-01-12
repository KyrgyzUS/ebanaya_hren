import os
import psycopg2
from psycopg2 import sql
from dotenv import load_dotenv
load_dotenv()

# Подключение к базе данных
def get_db_connection():
    DATABASE_URL = os.getenv('DATABASE_URL')
    if not DATABASE_URL:
        raise EnvironmentError("DATABASE_URL не установлена в переменных окружения.")
    try:
        conn = psycopg2.connect(DATABASE_URL, sslmode='require')
        return conn
    except Exception as e:
        raise ConnectionError(f"Не удалось подключиться к базе данных: {e}")

# Удаление всех таблиц
def drop_all_tables():
    conn = get_db_connection()
    cursor = conn.cursor()
    try:
        # Получение всех таблиц из текущей схемы
        cursor.execute("""
            SELECT tablename FROM pg_tables
            WHERE schemaname = 'public';
        """)
        tables = cursor.fetchall()

        # Удаление каждой таблицы
        for table in tables:
            print(f"Удаление таблицы {table[0]}...")
            cursor.execute(sql.SQL("DROP TABLE IF EXISTS {} CASCADE;").format(sql.Identifier(table[0])))

        conn.commit()
        print("Все таблицы успешно удалены!")
    except Exception as e:
        conn.rollback()
        print(f"Ошибка при удалении таблиц: {e}")
    finally:
        cursor.close()
        conn.close()

if __name__ == "__main__":
    try:
        drop_all_tables()
    except Exception as e:
        print(f"Произошла ошибка: {e}")
