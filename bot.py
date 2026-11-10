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
from aiogram.types import (
    WebAppInfo, InlineKeyboardMarkup, InlineKeyboardButton,
    Message, CallbackQuery
)
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
TOKEN = os.environ.get("BOT_TOKEN", "").strip()
if not TOKEN:
    raise ValueError("❌ Не задан BOT_TOKEN (переменная окружения).")

WEBAPP_URL = os.environ.get("WEBAPP_URL", "https://bot-1794065452-4003-noom.bothost.tech")
ADMIN_ID = int(os.environ.get("ADMIN_ID", "7714111634"))
OPERATOR = "@sai_helper"

logging.basicConfig(level=logging.INFO)
bot = Bot(token=TOKEN, default=DefaultBotProperties(parse_mode="HTML"))
dp = Dispatcher(storage=MemoryStorage())
scheduler = AsyncIOScheduler(timezone="Europe/Moscow")

# ================= БАЗА =================
DATA_DIR = os.environ.get("DATA_DIR", "/app/data")
os.makedirs(DATA_DIR, exist_ok=True)
DB_FILE = os.path.join(DATA_DIR, "fitplan.db")
OLD_JSON_PATH = os.path.join(DATA_DIR, "users.json")


async def init_db():
    async with aiosqlite.connect(DB_FILE) as db:
        await db.execute("""CREATE TABLE IF NOT EXISTS users (
            user_id INTEGER PRIMARY KEY, username TEXT, name TEXT, age INTEGER, sex TEXT,
            weight REAL, height REAL, activity REAL, goal TEXT, kcal_target REAL, protein_target REAL,
            fat_target REAL, carb_target REAL, balance REAL DEFAULT 0, sub_until INTEGER,
            referred_by INTEGER, ref_count INTEGER DEFAULT 0, ref_earned INTEGER DEFAULT 0,
            streak_count INTEGER DEFAULT 0, last_workout_date TEXT,
            timezone TEXT DEFAULT 'Europe/Moscow', created_at TEXT,
            foods_json TEXT DEFAULT '[]', plans_json TEXT DEFAULT '{}',
            today_workouts_json TEXT DEFAULT '[]', custom_workouts_json TEXT DEFAULT '[]',
            streak_json TEXT DEFAULT '[false,false,false,false,false,false,false]',
            water_today INTEGER DEFAULT 0,
            sleep_json TEXT DEFAULT '{}',
            weight_log_json TEXT DEFAULT '[]',
            mood_json TEXT DEFAULT '{}',
            achievements_json TEXT DEFAULT '[]',
            meal_templates_json TEXT DEFAULT '[]',
            photos_json TEXT DEFAULT '[]',
            trial_used INTEGER DEFAULT 0
        )""")

        # Добавляем колонки, если таблица уже старая
        new_cols = [
            ("foods_json", "TEXT DEFAULT '[]'"),
            ("plans_json", "TEXT DEFAULT '{}'"),
            ("today_workouts_json", "TEXT DEFAULT '[]'"),
            ("custom_workouts_json", "TEXT DEFAULT '[]'"),
            ("streak_json", "TEXT DEFAULT '[false,false,false,false,false,false,false]'"),
            ("water_today", "INTEGER DEFAULT 0"),
            ("sleep_json", "TEXT DEFAULT '{}'"),
            ("weight_log_json", "TEXT DEFAULT '[]'"),
            ("mood_json", "TEXT DEFAULT '{}'"),
            ("achievements_json", "TEXT DEFAULT '[]'"),
            ("meal_templates_json", "TEXT DEFAULT '[]'"),
            ("photos_json", "TEXT DEFAULT '[]'"),
            ("trial_used", "INTEGER DEFAULT 0"),
        ]
        for col, col_type in new_cols:
            try:
                await db.execute(f"ALTER TABLE users ADD COLUMN {col} {col_type}")
            except aiosqlite.OperationalError:
                pass

        # Глобальные настройки (время уведомлений)
        await db.execute("""CREATE TABLE IF NOT EXISTS settings (
            key TEXT PRIMARY KEY, value TEXT)""")

        # Логи
        await db.execute("""CREATE TABLE IF NOT EXISTS food_log (
            id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER, date TEXT, ts TEXT,
            name TEXT, grams REAL, kcal REAL, protein REAL, fat REAL, carb REAL)""")
        await db.execute("""CREATE TABLE IF NOT EXISTS workouts_log (
            id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER, date TEXT, plan_id TEXT,
            plan_name TEXT, duration_min INTEGER, calories_burned REAL, exercises_json TEXT)""")
        await db.execute("""CREATE TABLE IF NOT EXISTS weight_log (
            id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER, date TEXT, weight REAL, ts TEXT)""")

        await db.commit()

        # Дефолтные настройки
        defaults = {
            "reminder_workout_time": "18:00",
            "reminder_streak_time": "20:00",
            "reminder_sub_time": "12:00",
        }
        for k, v in defaults.items():
            await db.execute("INSERT OR IGNORE INTO settings (key, value) VALUES (?, ?)", (k, v))
        await db.commit()

    # Миграция users.json → SQLite
    if os.path.exists(OLD_JSON_PATH):
        try:
            with open(OLD_JSON_PATH, "r", encoding="utf-8") as f:
                old_db = json.load(f)
            async with aiosqlite.connect(DB_FILE) as db:
                for uid, u in old_db.items():
                    prof = u.get("profile", {})
                    await db.execute(
                        """INSERT OR IGNORE INTO users
                        (user_id, username, name, age, sex, weight, height, activity, goal,
                         kcal_target, balance, sub_until, referred_by, ref_count, ref_earned, created_at)
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                        (
                            int(uid), u.get("tg_username"), u.get("name"), prof.get("age"),
                            prof.get("sex"), prof.get("weight"), prof.get("height"),
                            prof.get("activity"), prof.get("goal"), prof.get("kcal"),
                            u.get("balance", 0), u.get("sub_until", 0),
                            u.get("referred_by"), u.get("ref_count", 0),
                            u.get("ref_earned", 0), datetime.utcnow().isoformat()
                        ),
                    )
                await db.commit()
            os.rename(OLD_JSON_PATH, OLD_JSON_PATH + ".migrated")
            print("✅ Миграция users.json → SQLite завершена")
        except Exception as e:
            print(f"⚠️ Ошибка миграции: {e}")


async def get_setting(key, default=""):
    async with aiosqlite.connect(DB_FILE) as db:
        async with db.execute("SELECT value FROM settings WHERE key=?", (key,)) as cur:
            row = await cur.fetchone()
            return row[0] if row else default


async def set_setting(key, value):
    async with aiosqlite.connect(DB_FILE) as db:
        await db.execute("INSERT OR REPLACE INTO settings (key, value) VALUES (?, ?)", (key, value))
        await db.commit()


def _parse(val, default):
    if not val:
        return default
    try:
        return json.loads(val)
    except Exception:
        return default


async def get_user_full(uid: str):
    async with aiosqlite.connect(DB_FILE) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute("SELECT * FROM users WHERE user_id = ?", (int(uid),)) as cur:
            row = await cur.fetchone()
            if not row:
                return None
            u = dict(row)
            u["profile"] = {
                "name": u.get("name", ""), "age": u.get("age", 16),
                "sex": u.get("sex") or "male", "weight": u.get("weight", 65),
                "height": u.get("height", 175), "activity": u.get("activity", 1.375),
                "goal": u.get("goal", "gain"), "kcal": u.get("kcal_target", 0),
            }
            u["foods"] = _parse(u.get("foods_json"), [])
            u["plans"] = _parse(u.get("plans_json"), {})
            u["todayWorkouts"] = _parse(u.get("today_workouts_json"), [])
            u["customWorkouts"] = _parse(u.get("custom_workouts_json"), [])
            u["streak"] = _parse(u.get("streak_json"), [False] * 7)
            u["waterToday"] = u.get("water_today") or 0
            u["sleepHistory"] = _parse(u.get("sleep_json"), {})
            u["weightHistory"] = _parse(u.get("weight_log_json"), [])
            u["moodHistory"] = _parse(u.get("mood_json"), {})
            u["achievements"] = _parse(u.get("achievements_json"), [])
            u["mealTemplates"] = _parse(u.get("meal_templates_json"), [])
            u["photos"] = _parse(u.get("photos_json"), [])
            u["sub_until"] = u.get("sub_until") or 0
            u["trial_used"] = u.get("trial_used") or 0
            return u


# ================= НАПОМИНАНИЯ =================
async def send_reminders():
    now = datetime.now()
    cur_time = now.strftime("%H:%M")
    today = now.strftime("%Y-%m-%d")
    t_workout = await get_setting("reminder_workout_time", "18:00")
    t_streak = await get_setting("reminder_streak_time", "20:00")
    t_sub = await get_setting("reminder_sub_time", "12:00")

    async with aiosqlite.connect(DB_FILE) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            "SELECT user_id, streak_count, last_workout_date, sub_until FROM users"
        ) as cur:
            users = await cur.fetchall()
            for u in users:
                uid = u["user_id"]
                if cur_time == t_workout and u["last_workout_date"] != today:
                    try:
                        await bot.send_message(uid, "💪 Пора на тренировку! Открой FitPlan.",
                                               reply_markup=get_main_menu())
                    except Exception:
                        pass
                if cur_time == t_streak and u["streak_count"] > 0 and u["last_workout_date"] != today:
                    try:
                        await bot.send_message(uid, f"🔥 Твой стрик {u['streak_count']} под угрозой!")
                    except Exception:
                        pass
                if cur_time == t_sub and u["sub_until"] and \
                   0 < u["sub_until"] - int(time.time()) < 86400:
                    try:
                        await bot.send_message(uid, "⏳ Подписка заканчивается завтра.")
                    except Exception:
                        pass


# ================= API (AIOHTTP) =================
async def handle_index(request):
    response = web.FileResponse("./public/index.html")
    response.headers["Cache-Control"] = "no-cache, no-store, must-revalidate"
    return response


async def handle_static(request):
    filepath = os.path.join("./public", request.match_info["filename"])
    return web.FileResponse(filepath) if os.path.exists(filepath) else web.Response(status=404)


async def api_get_user(request):
    user = await get_user_full(request.query.get("id"))
    return web.json_response(user) if user else web.json_response({"error": "Not found"}, status=404)


async def api_save_all(request):
    data = await request.json()
    uid = str(data.get("id"))
    if not uid:
        return web.json_response({"error": "No ID"}, status=400)
    now = int(time.time())

    async with aiosqlite.connect(DB_FILE) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute("SELECT sub_until FROM users WHERE user_id = ?", (int(uid),)) as cur:
            row = await cur.fetchone()

        # Проверка подписки
        if row and (not row["sub_until"] or row["sub_until"] < now):
            return web.json_response({"error": "Subscription expired"}, status=403)

        if not row:
            await db.execute(
                "INSERT INTO users (user_id, name, sub_until, created_at, trial_used) VALUES (?, ?, ?, ?, ?)",
                (int(uid), "User", 0, datetime.utcnow().isoformat(), 0),
            )

        if "profile" in data:
            p = data["profile"]
            await db.execute(
                """UPDATE users SET name=?, age=?, sex=?, weight=?, height=?, activity=?, goal=?, kcal_target=?
                   WHERE user_id=?""",
                (p.get("name"), p.get("age"), p.get("sex") or p.get("gender"), p.get("weight"),
                 p.get("height"), p.get("activity"), p.get("goal"), p.get("kcal"), int(uid)),
            )

        field_map = {
            "foods": "foods_json",
            "plans": "plans_json",
            "todayWorkouts": "today_workouts_json",
            "customWorkouts": "custom_workouts_json",
            "streak": "streak_json",
            "waterToday": "water_today",
            "sleepHistory": "sleep_json",
            "weightHistory": "weight_log_json",
            "moodHistory": "mood_json",
            "achievements": "achievements_json",
            "mealTemplates": "meal_templates_json",
            "photos": "photos_json",
        }
        for key, col in field_map.items():
            if key in data:
                val = data[key]
                if col == "water_today":
                    await db.execute(f"UPDATE users SET {col}=? WHERE user_id=?", (int(val or 0), int(uid)))
                else:
                    await db.execute(f"UPDATE users SET {col}=? WHERE user_id=?",
                                     (json.dumps(val, ensure_ascii=False), int(uid)))

        await db.commit()
    return web.json_response({"status": "ok"})


async def api_buy_sub(request):
    data = await request.json()
    uid = int(data.get("id"))
    async with aiosqlite.connect(DB_FILE) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute("SELECT balance, sub_until FROM users WHERE user_id=?", (uid,)) as cur:
            row = await cur.fetchone()
        if not row:
            return web.json_response({"error": "Not found"}, status=404)
        if row["balance"] < 499:
            return web.json_response({"error": "Not enough money"}, status=400)
        new_until = max(row["sub_until"] or 0, int(time.time())) + 30 * 24 * 3600
        await db.execute(
            "UPDATE users SET balance = balance - 499, sub_until = ? WHERE user_id = ?",
            (new_until, uid),
        )
        await db.commit()
    return web.json_response({"status": "ok", "sub_until": new_until})


async def api_weight_add(request):
    data = await request.json()
    uid = int(data.get("id"))
    weight = float(data.get("weight"))
    today = datetime.now().strftime("%Y-%m-%d")
    async with aiosqlite.connect(DB_FILE) as db:
        await db.execute(
            "INSERT INTO weight_log (user_id, date, weight, ts) VALUES (?, ?, ?, ?)",
            (uid, today, weight, datetime.now().isoformat()),
        )
        await db.execute("UPDATE users SET weight = ? WHERE user_id = ?", (weight, uid))
        await db.commit()
    return web.json_response({"status": "ok"})


async def api_weight_history(request):
    uid = int(request.query.get("id"))
    async with aiosqlite.connect(DB_FILE) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            "SELECT date, weight FROM weight_log WHERE user_id=? ORDER BY date ASC", (uid,)
        ) as cur:
            rows = await cur.fetchall()
            return web.json_response([dict(r) for r in rows])


async def api_export_csv(request):
    uid = int(request.query.get("id"))
    type_ = request.query.get("type", "food")
    table = "food_log" if type_ == "food" else "workouts_log"
    async with aiosqlite.connect(DB_FILE) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(f"SELECT * FROM {table} WHERE user_id=?", (uid,)) as cur:
            rows = await cur.fetchall()
    output = io.StringIO()
    if rows:
        writer = csv.DictWriter(output, fieldnames=rows[0].keys())
        writer.writeheader()
        for r in rows:
            writer.writerow(dict(r))
    return web.Response(
        text=output.getvalue(), content_type="text/csv",
        headers={"Content-Disposition": f'attachment; filename="{type_}.csv"'},
    )


async def api_export_pdf(request):
    uid = int(request.query.get("id"))
    buffer = io.BytesIO()
    p = canvas.Canvas(buffer, pagesize=A4)
    p.drawString(100, 800, f"FitPlan Report — user {uid}")
    p.drawString(100, 780, f"Date: {datetime.now().strftime('%Y-%m-%d')}")
    y = 740
    async with aiosqlite.connect(DB_FILE) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute("SELECT * FROM workouts_log WHERE user_id=?", (uid,)) as cur:
            for r in await cur.fetchall():
                p.drawString(100, y, f"{r['date']} — {r['plan_name']} ({r['duration_min']} мин)")
                y -= 20
                if y < 50:
                    p.showPage()
                    y = 800
    p.save()
    buffer.seek(0)
    return web.Response(
        body=buffer.read(), content_type="application/pdf",
        headers={"Content-Disposition": 'attachment; filename="report.pdf"'},
    )


async def start_web_server():
    app = web.Application(client_max_size=20 * 1024 * 1024)  # 20 MB для фото
    app.router.add_get("/", handle_index)
    app.router.add_get("/api/user", api_get_user)
    app.router.add_post("/api/save", api_save_all)
    app.router.add_post("/api/buy_sub", api_buy_sub)
    app.router.add_post("/api/weight/add", api_weight_add)
    app.router.add_get("/api/weight/history", api_weight_history)
    app.router.add_get("/api/export/csv", api_export_csv)
    app.router.add_get("/api/export/pdf", api_export_pdf)
    app.router.add_get("/{filename}", handle_static)

    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "0.0.0.0", 3000)
    await site.start()
    print("✅ Web server on :3000")


# ================= БОТ =================
def get_main_menu():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🏋️ Открыть FitPlan", web_app=WebAppInfo(url=WEBAPP_URL))],
        [InlineKeyboardButton(text="💳 Подписка", callback_data="menu_sub")],
        [InlineKeyboardButton(text="👥 Реферальная система", callback_data="menu_ref")],
    ])


@dp.message(Command("start"))
async def cmd_start(message: types.Message):
    uid = message.from_user.id
    async with aiosqlite.connect(DB_FILE) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute("SELECT user_id FROM users WHERE user_id=?", (uid,)) as cur:
            row = await cur.fetchone()
        if not row:
            await db.execute(
                "INSERT INTO users (user_id, name, username, sub_until, created_at, trial_used) VALUES (?,?,?,?,?,?)",
                (uid, message.from_user.first_name, message.from_user.username, 0,
                 datetime.utcnow().isoformat(), 0),
            )
            await db.commit()
    await message.answer(
        "Привет! Это <b>FitPlan</b>.\n\nТвой личный тренер и дневник питания.",
        reply_markup=get_main_menu(),
    )


@dp.callback_query(F.data == "menu_sub")
async def cb_sub_info(callback: CallbackQuery):
    async with aiosqlite.connect(DB_FILE) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            "SELECT balance, sub_until, trial_used FROM users WHERE user_id=?",
            (callback.from_user.id,),
        ) as cur:
            row = await cur.fetchone()
    balance = row["balance"] if row else 0
    sub_until = row["sub_until"] if row else 0
    trial_used = row["trial_used"] if row else 0
    now = int(time.time())
    status = "❌ Не активна"
    if sub_until and sub_until > now:
        status = f"✅ Активна ({math.ceil((sub_until - now) / 86400)} дн.)"
    text = (
        f"💳 <b>Подписка</b>\n\n"
        f"Баланс: <b>{balance} ₽</b>\n"
        f"Статус: <b>{status}</b>\n\n"
        f"Цена: <s>999 ₽</s> <b>299 ₽</b> / мес\n"
    )
    buttons = []
    if not trial_used or callback.from_user.id == ADMIN_ID:
        buttons.append([InlineKeyboardButton(text="🎁 Пробный период (3 дня)", callback_data="activate_trial")])
    buttons += [
        [InlineKeyboardButton(text="💳 Оплатить с баланса", callback_data="pay_sub")],
        [InlineKeyboardButton(text="👨‍💻 Оператор", url=f"https://t.me/{OPERATOR.replace('@','')}")],
        [InlineKeyboardButton(text="⬅️ Назад", callback_data="menu_main")],
    ]
    await callback.message.edit_text(text, reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons))


@dp.callback_query(F.data == "activate_trial")
async def cb_activate_trial(callback: CallbackQuery):
    uid = callback.from_user.id
    async with aiosqlite.connect(DB_FILE) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            "SELECT trial_used, sub_until FROM users WHERE user_id=?", (uid,)
        ) as cur:
            row = await cur.fetchone()
        if not row:
            return await callback.answer("❌ Не найден", show_alert=True)
        if row["trial_used"] and uid != ADMIN_ID:
            return await callback.answer("❌ Уже использовал", show_alert=True)
        now = int(time.time())
        new_until = max(row["sub_until"] or 0, now) + 3 * 86400
        await db.execute(
            "UPDATE users SET sub_until=?, trial_used=1 WHERE user_id=?", (new_until, uid)
        )
        await db.commit()
    await callback.answer("🎁 Пробный период на 3 дня активирован!", show_alert=True)
    await cb_sub_info(callback)


@dp.callback_query(F.data == "pay_sub")
async def cb_pay_sub(callback: CallbackQuery):
    async with aiosqlite.connect(DB_FILE) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            "SELECT balance, sub_until FROM users WHERE user_id=?", (callback.from_user.id,)
        ) as cur:
            row = await cur.fetchone()
        if not row:
            return await callback.answer("Ошибка", show_alert=True)
        if row["balance"] < 499:
            return await callback.answer("Недостаточно средств", show_alert=True)
        new_until = max(row["sub_until"] or 0, int(time.time())) + 30 * 86400
        await db.execute(
            "UPDATE users SET balance=balance-499, sub_until=? WHERE user_id=?",
            (new_until, callback.from_user.id),
        )
        await db.commit()
    await callback.answer("✅ Подписка оплачена", show_alert=True)
    await cb_sub_info(callback)


@dp.callback_query(F.data == "menu_ref")
async def cb_ref_info(callback: CallbackQuery):
    async with aiosqlite.connect(DB_FILE) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            "SELECT ref_count, ref_earned FROM users WHERE user_id=?", (callback.from_user.id,)
        ) as cur:
            row = await cur.fetchone()
    ref_count = row["ref_count"] if row else 0
    ref_earned = row["ref_earned"] if row else 0
    bot_username = (await bot.get_me()).username
    link = f"https://t.me/{bot_username}?start=ref_{callback.from_user.id}"
    text = (
        f"👥 <b>Реферальная система</b>\n\n"
        f"Приглашай друзей и получай 10% с их пополнений.\n\n"
        f"Твоя ссылка:\n<code>{link}</code>\n\n"
        f"Друзей: <b>{ref_count}</b>\nЗаработано: <b>{ref_earned} ₽</b>"
    )
    await callback.message.edit_text(
        text,
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="⬅️ Назад", callback_data="menu_main")]
        ]),
    )


@dp.callback_query(F.data == "menu_main")
async def cb_main_menu(callback: CallbackQuery):
    await callback.message.edit_text(
        "Привет! Это <b>FitPlan</b>.", reply_markup=get_main_menu()
    )


# ================= АДМИНКА =================
class AdminState(StatesGroup):
    balance_user = State()
    balance_amount = State()
    broadcast = State()
    reset_sub_user = State()
    reminder_time = State()


def admin_menu():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="💰 Начислить баланс", callback_data="admin_balance")],
        [InlineKeyboardButton(text="⛔ Обнулить подписку", callback_data="admin_reset_sub")],
        [InlineKeyboardButton(text="⏰ Настройки уведомлений", callback_data="admin_reminders")],
        [InlineKeyboardButton(text="📋 Пользователи", callback_data="admin_users")],
        [InlineKeyboardButton(text="📢 Рассылка", callback_data="admin_broadcast")],
    ])


@dp.message(Command("admin"))
async def cmd_admin(message: Message):
    if message.from_user.id != ADMIN_ID:
        return await message.answer("⛔ Нет доступа.")
    await message.answer("👑 <b>Админ-панель</b>", reply_markup=admin_menu())


@dp.callback_query(F.data == "admin_balance")
async def cb_admin_balance(callback: CallbackQuery, state: FSMContext):
    if callback.from_user.id != ADMIN_ID:
        return
    await callback.message.edit_text("Введи <b>ID пользователя</b>:")
    await state.set_state(AdminState.balance_user)


@dp.message(AdminState.balance_user)
async def process_balance_user(message: Message, state: FSMContext):
    if message.from_user.id != ADMIN_ID:
        return
    target_id = message.text.strip()
    async with aiosqlite.connect(DB_FILE) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute("SELECT name FROM users WHERE user_id=?", (int(target_id),)) as cur:
            row = await cur.fetchone()
    if not row:
        await message.answer("❌ Не найден")
        return await state.clear()
    await state.update_data(target_id=target_id)
    await message.answer(f"Введи <b>сумму</b> для <code>{row['name']}</code>:")
    await state.set_state(AdminState.balance_amount)


@dp.message(AdminState.balance_amount)
async def process_balance_amount(message: Message, state: FSMContext):
    if message.from_user.id != ADMIN_ID:
        return
    try:
        amount = int(message.text.strip())
        data = await state.get_data()
        target_id = int(data["target_id"])
        async with aiosqlite.connect(DB_FILE) as db:
            db.row_factory = aiosqlite.Row
            async with db.execute("SELECT referred_by FROM users WHERE user_id=?", (target_id,)) as cur:
                row = await cur.fetchone()
            ref = row["referred_by"] if row else None
            await db.execute("UPDATE users SET balance=balance+? WHERE user_id=?", (amount, target_id))
            if ref:
                bonus = int(amount * 0.1)
                await db.execute(
                    "UPDATE users SET balance=balance+?, ref_earned=ref_earned+?, ref_count=ref_count+1 WHERE user_id=?",
                    (bonus, bonus, ref),
                )
                try:
                    await bot.send_message(ref, f"🎉 Друг пополнил баланс! Тебе +{bonus} ₽")
                except Exception:
                    pass
            await db.commit()
        try:
            await bot.send_message(target_id, f"🎉 Баланс пополнен на {amount} ₽")
        except Exception:
            pass
        await message.answer(f"✅ Начислено {amount} ₽")
        await cmd_admin(message)
    except ValueError:
        await message.answer("❌ Введи число")
    finally:
        await state.clear()


@dp.callback_query(F.data == "admin_reset_sub")
async def cb_reset_sub(callback: CallbackQuery, state: FSMContext):
    if callback.from_user.id != ADMIN_ID:
        return
    await callback.message.edit_text("Введи <b>ID</b> пользователя:")
    await state.set_state(AdminState.reset_sub_user)


@dp.message(AdminState.reset_sub_user)
async def process_reset_sub(message: Message, state: FSMContext):
    if message.from_user.id != ADMIN_ID:
        return
    try:
        target_id = int(message.text.strip())
        async with aiosqlite.connect(DB_FILE) as db:
            await db.execute(
                "UPDATE users SET sub_until=0, trial_used=0 WHERE user_id=?", (target_id,)
            )
            await db.commit()
        try:
            await bot.send_message(target_id, "⚠️ Подписка обнулена администратором")
        except Exception:
            pass
        await message.answer(f"✅ Подписка <code>{target_id}</code> обнулена")
        await cmd_admin(message)
    except ValueError:
        await message.answer("❌ Введи число")
    finally:
        await state.clear()


# === Настройки уведомлений ===
@dp.callback_query(F.data == "admin_reminders")
async def cb_admin_reminders(callback: CallbackQuery):
    if callback.from_user.id != ADMIN_ID:
        return
    t_w = await get_setting("reminder_workout_time", "18:00")
    t_s = await get_setting("reminder_streak_time", "20:00")
    t_p = await get_setting("reminder_sub_time", "12:00")
    text = (
        f"⏰ <b>Уведомления (МСК)</b>\n\n"
        f"💪 Тренировка: <b>{t_w}</b>\n"
        f"🔥 Стрик: <b>{t_s}</b>\n"
        f"💳 Подписка: <b>{t_p}</b>\n\n"
        f"Выбери, что изменить:"
    )
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=f"💪 Тренировка ({t_w})", callback_data="edit_rem|reminder_workout_time")],
        [InlineKeyboardButton(text=f"🔥 Стрик ({t_s})", callback_data="edit_rem|reminder_streak_time")],
        [InlineKeyboardButton(text=f"💳 Подписка ({t_p})", callback_data="edit_rem|reminder_sub_time")],
        [InlineKeyboardButton(text="⬅️ Назад", callback_data="admin_back")],
    ])
    await callback.message.edit_text(text, reply_markup=kb)


@dp.callback_query(F.data.startswith("edit_rem|"))
async def cb_edit_rem(callback: CallbackQuery, state: FSMContext):
    if callback.from_user.id != ADMIN_ID:
        return
    key = callback.data.split("|")[1]
    await state.update_data(reminder_key=key)
    labels = {
        "reminder_workout_time": "тренировка",
        "reminder_streak_time": "стрик",
        "reminder_sub_time": "подписка",
    }
    await callback.message.edit_text(
        f"Введи время для «{labels[key]}» в формате <code>ЧЧ:ММ</code>:",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="⬅️ Отмена", callback_data="admin_reminders")]
        ]),
    )
    await state.set_state(AdminState.reminder_time)


@dp.message(AdminState.reminder_time)
async def process_reminder_time(message: Message, state: FSMContext):
    if message.from_user.id != ADMIN_ID:
        return
    txt = message.text.strip()
    if len(txt) != 5 or txt[2] != ":":
        return await message.answer("❌ Формат: <code>ЧЧ:ММ</code>")
    try:
        hh, mm = map(int, txt.split(":"))
        if not (0 <= hh < 24 and 0 <= mm < 60):
            raise ValueError
    except ValueError:
        return await message.answer("❌ Неверное время")
    data = await state.get_data()
    await set_setting(data["reminder_key"], txt)
    await state.clear()
    await message.answer(f"✅ Сохранено: <b>{txt}</b> (МСК)")
    await cmd_admin(message)


@dp.callback_query(F.data == "admin_users")
async def cb_admin_users(callback: CallbackQuery):
    if callback.from_user.id != ADMIN_ID:
        return
    async with aiosqlite.connect(DB_FILE) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute("SELECT * FROM users LIMIT 30") as cur:
            users = await cur.fetchall()
    text = "📋 <b>Пользователи</b>\n\n"
    for u in users:
        sub = "✅" if (u["sub_until"] and u["sub_until"] > time.time()) else "❌"
        text += f"👤 <b>{u['name']}</b> (ID <code>{u['user_id']}</code>) — {u['balance']} ₽ · {sub}\n"
    await callback.message.edit_text(
        text,
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="⬅️ Назад", callback_data="admin_back")]
        ]),
    )


@dp.callback_query(F.data == "admin_broadcast")
async def cb_admin_broadcast(callback: CallbackQuery, state: FSMContext):
    if callback.from_user.id != ADMIN_ID:
        return
    await callback.message.edit_text("Введи <b>текст рассылки</b>:")
    await state.set_state(AdminState.broadcast)


@dp.message(AdminState.broadcast)
async def process_broadcast(message: Message, state: FSMContext):
    if message.from_user.id != ADMIN_ID:
        return
    text = message.text
    async with aiosqlite.connect(DB_FILE) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute("SELECT user_id FROM users") as cur:
            users = await cur.fetchall()
    sent = failed = 0
    msg = await message.answer("🔄 Рассылка...")
    for u in users:
        try:
            await bot.send_message(u["user_id"], text)
            sent += 1
            await asyncio.sleep(0.05)
        except Exception:
            failed += 1
    await msg.edit_text(f"✅ Готово. Отправлено: {sent}, ошибок: {failed}")
    await state.clear()
    await cmd_admin(message)


@dp.callback_query(F.data == "admin_back")
async def cb_admin_back(callback: CallbackQuery):
    if callback.from_user.id != ADMIN_ID:
        return
    await callback.message.edit_text("👑 <b>Админ-панель</b>", reply_markup=admin_menu())


# ================= ЗАПУСК =================
async def main():
    await init_db()
    await start_web_server()
    scheduler.add_job(send_reminders, "cron", minute="*")
    scheduler.start()
    print("🚀 Бот и сервер запущены")
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
