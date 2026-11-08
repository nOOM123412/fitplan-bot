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
TOKEN = "8718686711:AAHX4NrsVn4CfhHhMJr6Z5hsuBdvIvdjF"
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
        await db.execute("""CREATE TABLE IF NOT EXISTS users (
            user_id INTEGER PRIMARY KEY, username TEXT, name TEXT, age INTEGER, sex TEXT,
            weight REAL, height REAL, activity REAL, goal TEXT, kcal_target REAL, protein_target REAL, 
            fat_target REAL, carb_target REAL, balance REAL DEFAULT 0, sub_until INTEGER, 
            referred_by INTEGER, ref_count INTEGER DEFAULT 0, ref_earned INTEGER DEFAULT 0,
            streak_count INTEGER DEFAULT 0, last_workout_date TEXT, timezone TEXT DEFAULT 'Europe/Moscow', created_at TEXT,
            foods_json TEXT DEFAULT '[]', plans_json TEXT DEFAULT '{}', today_workouts_json TEXT DEFAULT '[]', custom_workouts_json TEXT DEFAULT '[]', streak_json TEXT DEFAULT '[false,false,false,false,false,false,false]')""")
        await db.execute("""CREATE TABLE IF NOT EXISTS food_log (
            id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER, date TEXT, ts TEXT,
            name TEXT, grams REAL, kcal REAL, protein REAL, fat REAL, carb REAL)""")
        await db.execute("""CREATE TABLE IF NOT EXISTS workouts_log (
            id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER, date TEXT, plan_id TEXT, 
            plan_name TEXT, duration_min INTEGER, calories_burned REAL, exercises_json TEXT)""")
        await db.execute("""CREATE TABLE IF NOT EXISTS custom_plans (
            id TEXT PRIMARY KEY, owner_id INTEGER, name TEXT, description TEXT, exercises_json TEXT, is_public INTEGER DEFAULT 1, created_at TEXT)""")
        await db.commit()

    # Миграция из старого users.json (если есть)
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
            
            # Формируем структуру для фронтенда
            user['profile'] = {
                "name": user['name'], "age": user['age'], "sex": user['sex'], "weight": user['weight'],
                "height": user['height'], "activity": user['activity'], "goal": user['goal'], "kcal": user['kcal_target']
            }
            
            # Парсим JSON поля
            user['foods'] = json.loads(user['foods_json']) if user.get('foods_json') else []
            user['plans'] = json.loads(user['plans_json']) if user.get('plans_json') else {}
            user['todayWorkouts'] = json.loads(user['today_workouts_json']) if user.get('today_workouts_json') else []
            user['customWorkouts'] = json.loads(user['custom_workouts_json']) if user.get('custom_workouts_json') else []
            user['streak'] = json.loads(user['streak_json']) if user.get('streak_json') else [False]*7
            
            user['sub_until'] = user['sub_until'] or 0
            return user

# ================= ПЛАНИРОВЩИК (НАПОМИНАНИЯ) =================
async def send_reminders():
    now = datetime.now()
    current_time = now.strftime("%H:%M")
    today = now.strftime("%Y-%m-%d")
    async with aiosqlite.connect(DB_FILE) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute("SELECT user_id, name, streak_count, last_workout_date, sub_until FROM users") as cur:
            users = await cur.fetchall()
            for u in users:
                uid = u['user_id']
                if current_time == "18:00" and u['last_workout_date'] != today:
                    try: await bot.send_message(uid, "💪 Пора на тренировку! Не забудь открыть FitPlan.", reply_markup=get_main_menu())
                    except: pass
                if current_time == "20:00" and u['streak_count'] > 0 and u['last_workout_date'] != today:
                    try: await bot.send_message(uid, f"🔥 Твой стрик {u['streak_count']} под угрозой! Осталось 4 часа до полуночи.")
                    except: pass
                if current_time == "12:00" and u['sub_until'] and (u['sub_until'] - int(time.time())) < 86400 and u['sub_until'] > int(time.time()):
                    try: await bot.send_message(uid, "⏳ Твоя подписка заканчивается завтра! Продли её, чтобы не потерять прогресс.")
                    except: pass

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
        
        # Проверка подписки
        if row and row['sub_until'] != 0 and row['sub_until'] < now:
            return web.json_response({"error": "Subscription expired"}, status=403)
        
        # Создаем пользователя, если его нет
        if not row:
            await db.execute("INSERT INTO users (user_id, name, sub_until, created_at) VALUES (?, ?, ?, ?)",
                             (int(uid), "User", now + 259200, datetime.utcnow().isoformat()))
        
        # Обновляем профиль
        if 'profile' in data:
            p = data['profile']
            await db.execute("""UPDATE users SET name=?, age=?, sex=?, weight=?, height=?, activity=?, goal=?, kcal_target=? WHERE user_id=?""",
                             (p.get('name'), p.get('age'), p.get('sex') or p.get('gender'), p.get('weight'), p.get('height'), 
                              p.get('activity'), p.get('goal'), p.get('kcal'), int(uid)))
        
        # Сохраняем JSON-поля
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

async def api_buy_sub(request):
    data = await request.json()
    uid = int(data.get('id'))
    async with aiosqlite.connect(DB_FILE) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute("SELECT balance, sub_until FROM users WHERE user_id=?", (uid,)) as cur:
            row = await cur.fetchone()
        if not row: return web.json_response({"error": "Not found"}, status=404)
        if row['balance'] < 499: return web.json_response({"error": "Not enough money"}, status=400)
        
        new_until = max(row['sub_until'] or 0, int(time.time())) + 2592000
        await db.execute("UPDATE users SET balance = balance - 499, sub_until = ? WHERE user_id = ?", (new_until, uid))
        await db.commit()
    return web.json_response({"status": "ok", "sub_until": new_until})

async def api_get_history(request):
    uid = request.query.get('id')
    type_ = request.query.get('type')
    table = "food_log" if type_ == "food" else "workouts_log"
    async with aiosqlite.connect(DB_FILE) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(f"SELECT * FROM {table} WHERE user_id=? ORDER BY date DESC LIMIT 50", (int(uid),)) as cur:
            rows = await cur.fetchall()
            return web.json_response([dict(r) for r in rows])

async def api_export_csv(request):
    uid = request.query.get('id')
    type_ = request.query.get('type')
    table = "food_log" if type_ == "food" else "workouts_log"
    
    async with aiosqlite.connect(DB_FILE) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(f"SELECT * FROM {table} WHERE user_id=?", (int(uid),)) as cur:
            rows = await cur.fetchall()
    
    output = io.StringIO()
    if rows:
        writer = csv.DictWriter(output, fieldnames=rows[0].keys())
        writer.writeheader()
        for r in rows: writer.writerow(dict(r))
    
    return web.Response(text=output.getvalue(), content_type='text/csv', headers={'Content-Disposition': f'attachment; filename="{type_}.csv"'})

async def api_export_pdf(request):
    uid = request.query.get('id')
    buffer = io.BytesIO()
    p = canvas.Canvas(buffer, pagesize=A4)
    p.drawString(100, 800, f"FitPlan Report (User: {uid})")
    p.drawString(100, 780, f"Date: {datetime.now().strftime('%Y-%m-%d')}")
    
    async with aiosqlite.connect(DB_FILE) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute("SELECT * FROM workouts_log WHERE user_id=?", (int(uid),)) as cur:
            rows = await cur.fetchall()
            y = 740
            for r in rows:
                p.drawString(100, y, f"{r['date']} - {r['plan_name']} ({r['duration_min']} min)")
                y -= 20
                if y < 50: p.showPage(); y = 800
    p.save()
    buffer.seek(0)
    return web.Response(body=buffer.read(), content_type='application/pdf', headers={'Content-Disposition': 'attachment; filename="report.pdf"'})

async def api_get_calculated_plan(request):
    uid = request.query.get('id')
    plan_raw = json.loads(request.query.get('plan_json', '[]'))
    user = await get_user_full(uid)
    if user and user['profile']['weight']:
        from load_calc import calculate_weights
        calc_plan = calculate_weights(user['profile'], plan_raw)
        return web.json_response(calc_plan)
    return web.json_response(plan_raw)

async def api_share_plan(request):
    data = await request.json()
    pid = str(uuid.uuid4())[:8]
    async with aiosqlite.connect(DB_FILE) as db:
        await db.execute("INSERT INTO custom_plans (id, owner_id, name, description, exercises_json, created_at) VALUES (?,?,?,?,?,?)",
                         (pid, data['owner_id'], data['name'], data.get('desc',''), json.dumps(data['exercises']), datetime.utcnow().isoformat()))
        await db.commit()
    return web.json_response({"plan_id": pid, "link": f"{WEBAPP_URL}?plan={pid}"})

async def start_web_server():
    app = web.Application()
    app.router.add_get('/', handle_index)
    app.router.add_get('/api/user', api_get_user)
    app.router.add_post('/api/save', api_save_all)
    app.router.add_post('/api/buy_sub', api_buy_sub)
    app.router.add_get('/api/history', api_get_history)
    app.router.add_get('/api/export/csv', api_export_csv)
    app.router.add_get('/api/export/pdf', api_export_pdf)
    app.router.add_get('/api/calc_plan', api_get_calculated_plan)
    app.router.add_post('/api/plans/share', api_share_plan)
    app.router.add_get('/{filename}', handle_static)
    
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, '0.0.0.0', 3000)
    await site.start()

# ================= БОТ =================
def get_main_menu():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="Открыть FitPlan 🏋️", web_app=WebAppInfo(url=WEBAPP_URL))],
        [InlineKeyboardButton(text="💳 Подписка", callback_data="menu_sub")],
        [InlineKeyboardButton(text="👥 Реферальная система", callback_data="menu_ref")]
    ])

@dp.message(Command("start"))
async def cmd_start(message: types.Message):
    uid = message.from_user.id
    async with aiosqlite.connect(DB_FILE) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute("SELECT user_id FROM users WHERE user_id=?", (uid,)) as cur:
            if not await cur.fetchone():
                await db.execute("INSERT INTO users (user_id, name, username, sub_until, created_at) VALUES (?,?,?,?,?)",
                                 (uid, message.from_user.first_name, message.from_user.username, int(time.time())+259200, datetime.utcnow().isoformat()))
                await db.commit()
    await message.answer("Привет! Это <b>FitPlan</b>.\n\nТвой личный тренер и дневник питания.", reply_markup=get_main_menu())

# ... (Остальные хендлеры подписки и админки остаются без изменений) ...

# ================= ЗАПУСК =================
async def main():
    await init_db()
    await start_web_server()
    scheduler.add_job(send_reminders, 'cron', minute='*')
    scheduler.start()
    print("Бот и сервер запущены.")
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
