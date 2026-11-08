import os
import json
import time
import math
import asyncio
import logging
import csv
import io
import uuid
from datetime import datetime, timedelta
from aiogram import Bot, Dispatcher, types, F
from aiogram.filters import Command
from aiogram.types import WebAppInfo, InlineKeyboardMarkup, InlineKeyboardButton, Message, CallbackQuery, BufferedInputFile
from aiogram.client.default import DefaultBotProperties
from aiohttp import web
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.context import FSMContext
from aiogram.fsm.storage.memory import MemoryStorage
import aiosqlite
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from reportlab.pdfgen import canvas
from reportlab.lib.pagesizes import A4

# ================= НАСТРОЙКИ =================
TOKEN = "-"
WEBAPP_URL = "https://bot-1794065452-4003-noom.bothost.tech"
ADMIN_ID = 7714111634
OPERATOR = "@sai_helper"

logging.basicConfig(level=logging.INFO)
bot = Bot(token=TOKEN, default=DefaultBotProperties(parse_mode="HTML"))
dp = Dispatcher(storage=MemoryStorage())
scheduler = AsyncIOScheduler(timezone="Europe/Moscow")

# ================= БАЗА ДАННЫХ (SQLITE) =================
DATA_DIR = os.environ.get('DATA_DIR', '/app/data')
os.makedirs(DATA_DIR, exist_ok=True)
DB_FILE = os.path.join(DATA_DIR, 'fitplan.db')
OLD_JSON_PATH = os.path.join(DATA_DIR, 'users.json')

async def init_db():
    async with aiosqlite.connect(DB_FILE) as db:
        # Создаем таблицу со всеми колонками
        await db.execute("""CREATE TABLE IF NOT EXISTS users (
            user_id INTEGER PRIMARY KEY, username TEXT, name TEXT, age INTEGER, sex TEXT,
            weight REAL, height REAL, activity REAL, goal TEXT, kcal_target REAL, protein_target REAL, 
            fat_target REAL, carb_target REAL, balance REAL DEFAULT 0, sub_until INTEGER, 
            referred_by INTEGER, ref_count INTEGER DEFAULT 0, ref_earned INTEGER DEFAULT 0,
            streak_count INTEGER DEFAULT 0, last_workout_date TEXT, timezone TEXT DEFAULT 'Europe/Moscow', created_at TEXT,
            foods_json TEXT DEFAULT '[]', plans_json TEXT DEFAULT '{}', today_workouts_json TEXT DEFAULT '[]', custom_workouts_json TEXT DEFAULT '[]', streak_json TEXT DEFAULT '[false,false,false,false,false,false,false]')""")
        
        # Принудительно добавляем колонки, если таблица старая (SQLite не умеет ADD COLUMN IF NOT EXISTS)
        columns = [
            ("foods_json", "TEXT DEFAULT '[]'"),
            ("plans_json", "TEXT DEFAULT '{}'"),
            ("today_workouts_json", "TEXT DEFAULT '[]'"),
            ("custom_workouts_json", "TEXT DEFAULT '[]'"),
            ("streak_json", "TEXT DEFAULT '[false,false,false,false,false,false,false]'")
        ]
        for col, col_type in columns:
            try:
                await db.execute(f"ALTER TABLE users ADD COLUMN {col} {col_type}")
                print(f"Колонка {col} добавлена.")
            except aiosqlite.OperationalError:
                pass # Колонка уже существует
        
        await db.execute("""CREATE TABLE IF NOT EXISTS food_log (
            id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER, date TEXT, ts TEXT,
            name TEXT, grams REAL, kcal REAL, protein REAL, fat REAL, carb REAL)""")
        await db.execute("""CREATE TABLE IF NOT EXISTS workouts_log (
            id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER, date TEXT, plan_id TEXT, 
            plan_name TEXT, duration_min INTEGER, calories_burned REAL, exercises_json TEXT)""")
        await db.execute("""CREATE TABLE IF NOT EXISTS custom_plans (
            id TEXT PRIMARY KEY, owner_id INTEGER, name TEXT, description TEXT, exercises_json TEXT, is_public INTEGER DEFAULT 1, created_at TEXT)""")
        await db.commit()

    # Миграция из старого users.json
    if os.path.exists(OLD_JSON_PATH):
        print("Миграция данных в SQLite...")
        try:
            with open(OLD_JSON_PATH, 'r', encoding='utf-8') as f: old_db = json.load(f)
            async with aiosqlite.connect(DB_FILE) as db:
                for uid, u in old_db.items():
                    prof = u.get('profile', {})
                    await db.execute("""INSERT OR IGNORE INTO users 
                        (user_id, username, name, age, sex, weight, height, activity, goal, kcal_target, balance, sub_until, referred_by, ref_count, ref_earned, created_at)
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                        (int(uid), u.get('tg_username'), u.get('name'), prof.get('age'), prof.get('sex'), prof.get('weight'),
                         prof.get('height'), prof.get('activity'), prof.get('goal'), prof.get('kcal'),
                         u.get('balance', 0), u.get('sub_until', 0), u.get('referred_by'), u.get('ref_count', 0),
                         u.get('ref_earned', 0), datetime.utcnow().isoformat()))
                await db.commit()
            os.rename(OLD_JSON_PATH, OLD_JSON_PATH + ".migrated")
            print("Миграция успешна.")
        except Exception as e:
            print(f"Ошибка миграции: {e}")

async def get_user_full(uid: str):
    async with aiosqlite.connect(DB_FILE) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute("SELECT * FROM users WHERE user_id = ?", (int(uid),)) as cur:
            row = await cur.fetchone()
            if not row: return None
            
            user = dict(row)
            
            user['profile'] = {
                "name": user.get('name', ''), "age": user.get('age', 16), "sex": user.get('sex') or user.get('gender', 'male'), 
                "weight": user.get('weight', 65), "height": user.get('height', 175), "activity": user.get('activity', 1.375), 
                "goal": user.get('goal', 'gain'), "kcal": user.get('kcal_target', 0)
            }
            
            # Безопасный парсинг JSON
            def parse_json(val, default):
                if not val: return default
                try: return json.loads(val)
                except: return default

            user['foods'] = parse_json(user.get('foods_json'), [])
            user['plans'] = parse_json(user.get('plans_json'), {})
            user['todayWorkouts'] = parse_json(user.get('today_workouts_json'), [])
            user['customWorkouts'] = parse_json(user.get('custom_workouts_json'), [])
            user['streak'] = parse_json(user.get('streak_json'), [False]*7)
            
            user['sub_until'] = user.get('sub_until') or 0
            return user

# ================= API (AIOHTTP) =================
async def handle_index(request): return web.FileResponse('./public/index.html')
async def handle_static(request):
    filepath = os.path.join('./public', request.match_info['filename'])
    return web.FileResponse(filepath) if os.path.exists(filepath) else web.Response(status=404)

async def api_get_user(request):
    user = await get_user_full(request.query.get('id'))
    return web.json_response(user) if user else web.json_response({"error": "Not found"}, status=404)

async def api_save_all(request):
    data = await request.json()
    uid = str(data.get('id'))
    now = int(time.time())
    
    async with aiosqlite.connect(DB_FILE) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute("SELECT sub_until FROM users WHERE user_id = ?", (int(uid),)) as cur:
            row = await cur.fetchone()
        
        if row and row['sub_until'] != 0 and row['sub_until'] < now:
            return web.json_response({"error": "Subscription expired"}, status=403)
        
        if not row:
            await db.execute("INSERT INTO users (user_id, name, sub_until, created_at) VALUES (?, ?, ?, ?)",
                             (int(uid), "User", now + 259200, datetime.utcnow().isoformat()))
        
        if 'profile' in data:
            p = data['profile']
            await db.execute("""UPDATE users SET name=?, age=?, sex=?, weight=?, height=?, activity=?, goal=?, kcal_target=? WHERE user_id=?""",
                             (p.get('name'), p.get('age'), p.get('sex') or p.get('gender'), p.get('weight'), p.get('height'), 
                              p.get('activity'), p.get('goal'), p.get('kcal'), int(uid)))
        
        if 'foods' in data:
            await db.execute("UPDATE users SET foods_json = ? WHERE user_id = ?", (json.dumps(data['foods'], ensure_ascii=False), int(uid)))
        if 'plans' in data:
            await db.execute("UPDATE users SET plans_json = ? WHERE user_id = ?", (json.dumps(data['plans'], ensure_ascii=False), int(uid)))
        if 'todayWorkouts' in data:
            await db.execute("UPDATE users SET today_workouts_json = ? WHERE user_id = ?", (json.dumps(data['todayWorkouts'], ensure_ascii=False), int(uid)))
        if 'customWorkouts' in data:
            await db.execute("UPDATE users SET custom_workouts_json = ? WHERE user_id = ?", (json.dumps(data['customWorkouts'], ensure_ascii=False), int(uid)))
        if 'streak' in data:
            await db.execute("UPDATE users SET streak_json = ? WHERE user_id = ?", (json.dumps(data['streak'], ensure_ascii=False), int(uid)))
            
        await db.commit()
        
    return web.json_response({"status": "ok"})

# ... (Остальные API и функции бота оставляем без изменений, они не влияют на сохранение плана) ...

async def start_web_server():
    app = web.Application()
    app.router.add_get('/', handle_index)
    app.router.add_get('/api/user', api_get_user)
    app.router.add_post('/api/save', api_save_all)
    # ... остальные роуты
    app.router.add_get('/{filename}', handle_static)
    
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, '0.0.0.0', 3000)
    await site.start()

# ... (хендлеры бота) ...

async def main():
    await init_db()
    await start_web_server()
    scheduler.add_job(send_reminders, 'cron', minute='*')
    scheduler.start()
    print("Бот и сервер запущены.")
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
