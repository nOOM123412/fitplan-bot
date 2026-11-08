import os
import json
import time
import asyncio
import logging
from aiogram import Bot, Dispatcher, types, F
from aiogram.filters import Command
from aiogram.types import WebAppInfo, InlineKeyboardMarkup, InlineKeyboardButton, Message, CallbackQuery
from aiogram.client.default import DefaultBotProperties
from aiohttp import web
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.context import FSMContext
from aiogram.fsm.storage.memory import MemoryStorage

# ================= НАСТРОЙКИ =================
TOKEN = "+"
WEBAPP_URL = "https://bot-1794065452-4003-noom.bothost.tech"
ADMIN_ID = 7714111634
OPERATOR = "@sai_helper"

logging.basicConfig(level=logging.INFO)
bot = Bot(token=TOKEN, default=DefaultBotProperties(parse_mode="HTML"))
dp = Dispatcher(storage=MemoryStorage())

# ================= БАЗА =================
DATA_DIR = os.environ.get('DATA_DIR', '/app/data')
os.makedirs(DATA_DIR, exist_ok=True)
DB_FILE = os.path.join(DATA_DIR, 'users.json')

def load_db():
    if not os.path.exists(DB_FILE): return {}
    try:
        with open(DB_FILE, 'r', encoding='utf-8') as f: return json.load(f)
    except: return {}

def save_db(data):
    with open(DB_FILE, 'w', encoding='utf-8') as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

# ================= API =================
async def handle_index(request): 
    # Отдаем HTML. Cache-Control запрещает кэширование
    response = web.FileResponse('./public/index.html')
    response.headers['Cache-Control'] = 'no-cache, no-store, must-revalidate'
    return response

async def api_get_user(request):
    user_id = request.query.get('id')
    db = load_db()
    if user_id in db: return web.json_response(db[user_id])
    return web.json_response({"error": "User not found"}, status=404)

async def api_save_all(request):
    data = await request.json()
    user_id = str(data.get('id'))
    if not user_id: return web.json_response({"error": "No ID"}, status=400)
    
    db = load_db()
    if user_id not in db:
        db[user_id] = {"name": "User", "tg_username": "", "balance": 0, "sub_until": 0, "referred_by": None, "ref_count": 0, "ref_earned": 0}
    
    if 'profile' in data: db[user_id]['profile'] = data['profile']
    if 'foods' in data: db[user_id]['foods'] = data['foods']
    if 'plans' in data: db[user_id]['plans'] = data['plans']
    if 'streak' in data: db[user_id]['streak'] = data['streak']
    
    save_db(db)
    return web.json_response({"status": "ok"})

async def api_buy_sub(request):
    data = await request.json()
    user_id = str(data.get('id'))
    db = load_db()
    if user_id in db:
        if db[user_id].get('balance', 0) >= 499:
            db[user_id]['balance'] -= 499
            current_until = max(db[user_id].get('sub_until', 0), int(time.time()))
            db[user_id]['sub_until'] = current_until + (30 * 24 * 60 * 60)
            save_db(db)
            return web.json_response({"status": "ok", "new_balance": db[user_id]['balance'], "sub_until": db[user_id]['sub_until']})
        return web.json_response({"error": "Not enough money"}, status=400)
    return web.json_response({"error": "User not found"}, status=404)

async def start_web_server():
    app = web.Application()
    app.router.add_get('/', handle_index)
    app.router.add_get('/api/user', api_get_user)
    app.router.add_post('/api/save', api_save_all)
    app.router.add_post('/api/buy_sub', api_buy_sub)
    
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, '0.0.0.0', 3000)
    await site.start()

# ================= БОТ (ОСТАВЛЯЕМ ЛОГИКУ) =================
# ... (Вставь сюда весь код хендлеров и админки из предыдущего файла, он не менялся) ...

async def main():
    await start_web_server()
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
