import os
import asyncio
import logging
from aiogram import Bot, Dispatcher, types, F
from aiogram.filters import Command
from aiogram.types import WebAppInfo, InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.client.default import DefaultBotProperties
from aiohttp import web

# ================= НАСТРОЙКИ =================
# Токен жестко вписан, чтобы избежать ошибок с переменными
TOKEN = "-"
# Ссылка на твой домен BotHost
WEBAPP_URL = "https://bot-1794065452-4003-noom.bothost.tech"

logging.basicConfig(level=logging.INFO)
bot = Bot(token=TOKEN, default=DefaultBotProperties(parse_mode="HTML"))
dp = Dispatcher()

# ================= ВЕБ-СЕРВЕР =================
async def handle_index(request):
    """Отдаёт главную страницу Mini App"""
    return web.FileResponse('./public/index.html')

async def handle_static(request):
    """Отдаёт статические файлы (plans.json и т.д.)"""
    filename = request.match_info['filename']
    return web.FileResponse(f'./public/{filename}')

async def start_web_server():
    """Запускает веб-сервер на порту 3000"""
    app = web.Application()
    app.router.add_get('/', handle_index)
    app.router.add_get('/{filename}', handle_static)
    
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, '0.0.0.0', 3000)
    await site.start()
    logging.info("✅ Веб-сервер запущен на порту 3000")

# ================= ХЕНДЛЕРЫ БОТА =================
@dp.message(Command("start"))
async def cmd_start(message: types.Message):
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="Открыть FitPlan 🏋️", web_app=WebAppInfo(url=WEBAPP_URL))]
    ])
    await message.answer(
        "Привет! Это <b>FitPlan</b>.\n\n"
        "Твой личный тренер и дневник питания. Считай КБЖУ, выбирай планы тренировок и следи за стриками.",
        reply_markup=kb
    )

# ================= ЗАПУСК =================
async def main():
    # Сначала запускаем веб-сервер, чтобы он слушал порт 3000
    await start_web_server()
    # Затем запускаем бота
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
