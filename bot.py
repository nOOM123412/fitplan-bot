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
from load_calc import calculate_weights

# ================= НАСТРОЙКИ =================
TOKEN = "8718686711:"
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
            except aiosqlite.OperationalError:
                pass 
        
        await db.execute("""CREATE TABLE IF NOT EXISTS food_log (
            id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER, date TEXT, ts TEXT,
            name TEXT, grams REAL, kcal REAL, protein REAL, fat REAL, carb REAL)""")
        await db.execute("""CREATE TABLE IF NOT EXISTS workouts_log (
            id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER, date TEXT, plan_id TEXT, 
            plan_name TEXT, duration_min INTEGER, calories_burned REAL, exercises_json TEXT)""")
        await db.execute("""CREATE TABLE IF NOT EXISTS custom_plans (
            id TEXT PRIMARY KEY, owner_id INTEGER, name TEXT, description TEXT, exercises_json TEXT, is_public INTEGER DEFAULT 1, created_at TEXT)""")
        await db.commit()

    if os.path.exists(OLD_JSON_PATH):
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

@dp.callback_query(F.data == "menu_sub")
async def cb_sub_info(callback: CallbackQuery):
    async with aiosqlite.connect(DB_FILE) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute("SELECT balance, sub_until FROM users WHERE user_id=?", (callback.from_user.id,)) as cur:
            row = await cur.fetchone()
    
    balance = row['balance'] if row else 0
    sub_until = row['sub_until'] if row else 0
    now = int(time.time())
    status = "❌ Не активна"
    if sub_until > now:
        days = math.ceil((sub_until - now) / 86400)
        status = f"✅ Активна ({days} дн.)"
    
    text = (
        f"💳 <b>Твоя подписка</b>\n\n"
        f"Баланс: <b>{balance} ₽</b>\n"
        f"Статус: <b>{status}</b>\n\n"
        f"Стоимость подписки на 1 месяц:\n"
        f"<s>999 ₽</s> <b>499 ₽</b> (-50% 🎉)\n\n"
    )
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="💳 Оплатить с баланса", callback_data="pay_sub")],
        [InlineKeyboardButton(text="👨‍💻 Оператор", url=f"https://t.me/{OPERATOR.replace('@','')}")],
        [InlineKeyboardButton(text="⬅️ Назад", callback_data="menu_main")]
    ])
    await callback.message.edit_text(text, reply_markup=kb)

@dp.callback_query(F.data == "pay_sub")
async def cb_pay_sub(callback: CallbackQuery):
    async with aiosqlite.connect(DB_FILE) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute("SELECT balance, sub_until FROM users WHERE user_id=?", (callback.from_user.id,)) as cur:
            row = await cur.fetchone()
        
        if not row:
            return await callback.answer("Ошибка: пользователь не найден", show_alert=True)
        
        if row['balance'] < 499:
            return await callback.answer("Недостаточно средств на балансе!", show_alert=True)
        
        new_until = max(row['sub_until'] or 0, int(time.time())) + (30 * 24 * 60 * 60)
        await db.execute("UPDATE users SET balance = balance - 499, sub_until = ? WHERE user_id = ?", (new_until, callback.from_user.id))
        await db.commit()
    
    await callback.answer("Подписка оплачена!", show_alert=True)
    await cb_sub_info(callback)

@dp.callback_query(F.data == "menu_ref")
async def cb_ref_info(callback: CallbackQuery):
    async with aiosqlite.connect(DB_FILE) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute("SELECT ref_count, ref_earned FROM users WHERE user_id=?", (callback.from_user.id,)) as cur:
            row = await cur.fetchone()
    
    ref_count = row['ref_count'] if row else 0
    ref_earned = row['ref_earned'] if row else 0
    bot_username = (await bot.get_me()).username
    ref_link = f"https://t.me/{bot_username}?start=ref_{callback.from_user.id}"
    text = (f"👥 <b>Реферальная система</b>\n\nПриглашай друзей и получай <b>10%</b> с каждого их пополнения!\n\n"
            f"Твоя ссылка:\n<code>{ref_link}</code>\n\n"
            f"Приглашено друзей: <b>{ref_count}</b>\nЗаработано: <b>{ref_earned} ₽</b>")
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="⬅️ Назад", callback_data="menu_main")]
    ])
    await callback.message.edit_text(text, reply_markup=kb)

@dp.callback_query(F.data == "menu_main")
async def cb_main_menu(callback: CallbackQuery):
    text = "Привет! Это <b>FitPlan</b>.\n\nТвой личный тренер и дневник питания."
    await callback.message.edit_text(text, reply_markup=get_main_menu())

# ================= АДМИНКА =================
class AdminState(StatesGroup):
    waiting_for_balance_user = State()
    waiting_for_balance_amount = State()
    waiting_for_broadcast = State()

def get_admin_menu():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="💰 Начислить баланс", callback_data="admin_add_balance")],
        [InlineKeyboardButton(text="📋 Все пользователи", callback_data="admin_users_list")],
        [InlineKeyboardButton(text="📢 Сделать рассылку", callback_data="admin_broadcast")]
    ])

@dp.message(Command("admin"))
async def cmd_admin(message: Message):
    if message.from_user.id != ADMIN_ID: return await message.answer("⛔ Нет доступа.")
    await message.answer("👑 <b>Админ-панель</b>", reply_markup=get_admin_menu())

@dp.callback_query(F.data == "admin_add_balance")
async def cb_admin_balance(callback: CallbackQuery, state: FSMContext):
    if callback.from_user.id != ADMIN_ID: return
    await callback.message.edit_text("Введи <b>Telegram ID</b> пользователя:")
    await state.set_state(AdminState.waiting_for_balance_user)

@dp.message(AdminState.waiting_for_balance_user)
async def process_admin_user(message: Message, state: FSMContext):
    if message.from_user.id != ADMIN_ID: return
    target_id = message.text.strip()
    async with aiosqlite.connect(DB_FILE) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute("SELECT name FROM users WHERE user_id=?", (int(target_id),)) as cur:
            row = await cur.fetchone()
    if not row:
        await message.answer("❌ Пользователь не найден.")
        return await state.clear()
    await state.update_data(target_id=target_id)
    await message.answer(f"Введи <b>сумму</b> для <code>{row['name']}</code>:")
    await state.set_state(AdminState.waiting_for_balance_amount)

@dp.message(AdminState.waiting_for_balance_amount)
async def process_admin_amount(message: Message, state: FSMContext):
    if message.from_user.id != ADMIN_ID: return
    try:
        amount = int(message.text.strip())
        data = await state.get_data()
        target_id = int(data['target_id'])
        
        async with aiosqlite.connect(DB_FILE) as db:
            db.row_factory = aiosqlite.Row
            async with db.execute("SELECT referred_by FROM users WHERE user_id=?", (target_id,)) as cur:
                row = await cur.fetchone()
            
            ref_by = row['referred_by'] if row else None
            await db.execute("UPDATE users SET balance = balance + ? WHERE user_id = ?", (amount, target_id))
            
            if ref_by:
                bonus = int(amount * 0.10)
                await db.execute("UPDATE users SET balance = balance + ?, ref_earned = ref_earned + ?, ref_count = ref_count + 1 WHERE user_id = ?", (bonus, bonus, ref_by))
                try: await bot.send_message(ref_by, f"🎉 Друг пополнил баланс! Тебе +{bonus} ₽.")
                except: pass
            
            await db.commit()
        
        try: await bot.send_message(target_id, f"🎉 Твой баланс пополнен на {amount} ₽!")
        except: pass
        await message.answer(f"✅ Баланс пополнен на {amount} ₽.")
        await cmd_admin(message)
    except ValueError: await message.answer("❌ Введи число.")
    finally: await state.clear()

@dp.callback_query(F.data == "admin_users_list")
async def cb_admin_users_list(callback: CallbackQuery):
    if callback.from_user.id != ADMIN_ID: return
    async with aiosqlite.connect(DB_FILE) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute("SELECT * FROM users LIMIT 30") as cur:
            users = await cur.fetchall()
    
    if not users: return await callback.message.edit_text("База пуста.")
    text = "📋 <b>Пользователи:</b>\n\n"
    for u in users:
        un = f" (@{u['username']})" if u['username'] else ""
        text += f"👤 <b>{u['name']}</b>{un}\n🆔 <code>{u['user_id']}</code> | 💰 {u['balance']} ₽\n\n"
    kb = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="⬅️ Назад", callback_data="admin_back")]])
    await callback.message.edit_text(text, reply_markup=kb)

@dp.callback_query(F.data == "admin_broadcast")
async def cb_admin_broadcast(callback: CallbackQuery, state: FSMContext):
    if callback.from_user.id != ADMIN_ID: return
    await callback.message.edit_text("Введи <b>текст для рассылки</b> (он уйдет всем пользователям):")
    await state.set_state(AdminState.waiting_for_broadcast)

@dp.message(AdminState.waiting_for_broadcast)
async def process_broadcast(message: Message, state: FSMContext):
    if message.from_user.id != ADMIN_ID: return
    text = message.text
    
    async with aiosqlite.connect(DB_FILE) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute("SELECT user_id FROM users") as cur:
            users = await cur.fetchall()
    
    sent = 0
    failed = 0
    msg = await message.answer("🔄 Начинаю рассылку...")
    
    for u in users:
        try:
            await bot.send_message(u['user_id'], text)
            sent += 1
            await asyncio.sleep(0.05)
        except:
            failed += 1
            
    await msg.edit_text(f"✅ <b>Рассылка завершена</b>\n\nОтправлено: {sent}\nОшибок: {failed}")
    await state.clear()
    await cmd_admin(message)

@dp.callback_query(F.data == "admin_back")
async def cb_admin_back(callback: CallbackQuery):
    if callback.from_user.id != ADMIN_ID: return
    await callback.message.edit_text("👑 <b>Админ-панель</b>", reply_markup=get_admin_menu())

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
