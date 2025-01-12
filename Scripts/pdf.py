import gspread
from oauth2client.service_account import ServiceAccountCredentials
import pdfkit
import os
from aiogram import Bot, Dispatcher, types
from aiogram.types import InputFile
from aiogram.utils import executor

API_TOKEN = 'YOUR_TELEGRAM_BOT_API_TOKEN'
bot = Bot(token=API_TOKEN)
dp = Dispatcher(bot)

# Настройка доступа к Google Sheets API
scope = ["https://spreadsheets.google.com/feeds", 'https://www.googleapis.com/auth/drive']
creds = ServiceAccountCredentials.from_json_keyfile_name('credentials.json', scope)
client = gspread.authorize(creds)

async def send_sheet_as_pdf(message: types.Message, sheet_url: str):
    # Открываем Google Sheet по URL
    sheet = client.open_by_url(sheet_url)
    worksheet = sheet.get_worksheet(0)  # Получаем первый лист

    # Получаем HTML представление листа
    html_content = worksheet.get_all_values()
    html = '<html><body><table>'
    for row in html_content:
        html += '<tr>'
        for cell in row:
            html += f'<td>{cell}</td>'
        html += '</tr>'
    html += '</table></body></html>'

    # Сохраняем HTML в файл
    with open('sheet.html', 'w', encoding='utf-8') as file:
        file.write(html)

    # Конвертируем HTML в PDF
    pdfkit.from_file('sheet.html', 'sheet.pdf')

    # Отправляем PDF файл пользователю
    pdf_file = InputFile('sheet.pdf')
    await message.answer_document(pdf_file)

    # Удаляем временные файлы
    os.remove('sheet.html')
    os.remove('sheet.pdf')

@dp.message_handler(commands=['start'])
async def send_welcome(message: types.Message):
    await message.reply("Отправьте ссылку на Google Таблицу, чтобы получить её в формате PDF.")

@dp.message_handler()
async def handle_message(message: types.Message):
    if 'docs.google.com/spreadsheets' in message.text:
        await send_sheet_as_pdf(message, message.text)
    else:
        await message.reply("Пожалуйста, отправьте корректную ссылку на Google Таблицу.")

if __name__ == '__main__':
    executor.start_polling(dp, skip_updates=True)
