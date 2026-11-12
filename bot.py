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
    raise ValueError("❌ Не задан BOT_TOKEN.")

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


async def init_db():
    async with aiosqlite.connect(DB_FILE) as db:
        await db.execute("PRAGMA journal_mode=WAL;")
        await db.commit()

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
            trial_used INTEGER DEFAULT 0,
            xp INTEGER DEFAULT 0,
            level INTEGER DEFAULT 1,
            friends_json TEXT DEFAULT '[]',
            recent_foods_json TEXT DEFAULT '[]',
            daily_quests_json TEXT DEFAULT '{}',
            reminders_json TEXT DEFAULT '{"water": true, "workout": true}',
            frame TEXT DEFAULT 'none'
        )""")

        new_cols = [
            ("xp", "INTEGER DEFAULT 0"), ("level", "INTEGER DEFAULT 1"),
            ("friends_json", "TEXT DEFAULT '[]'"), ("recent_foods_json", "TEXT DEFAULT '[]'"),
            ("daily_quests_json", "TEXT DEFAULT '{}'"), 
            ("reminders_json", "TEXT DEFAULT '{\"water\": true, \"workout\": true}'"),
            ("frame", "TEXT DEFAULT 'none'"),
            ("food_log_json", "TEXT DEFAULT '{}'") # Для копирования "вчера"
        ]
        for col, col_type in new_cols:
            try: await db.execute(f"ALTER TABLE users ADD COLUMN {col} {col_type}")
            except aiosqlite.OperationalError: pass

        await db.execute("""CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT)""")
        await db.execute("""CREATE TABLE IF NOT EXISTS food_log (id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER, date TEXT, ts TEXT, name TEXT, grams REAL, kcal REAL, protein REAL, fat REAL, carb REAL)""")
        await db.execute("""CREATE TABLE IF NOT EXISTS workouts_log (id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER, date TEXT, plan_id TEXT, plan_name TEXT, duration_min INTEGER, calories_burned REAL, exercises_json TEXT)""")
        await db.execute("""CREATE TABLE IF NOT EXISTS weight_log (id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER, date TEXT, weight REAL, ts TEXT)""")
        # Таблица истории упражнений
        await db.execute("""CREATE TABLE IF NOT EXISTS exercise_history (
            id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER, ex_name TEXT, weight REAL, reps INTEGER, date TEXT
        )""")
        await db.commit()

        defaults = {"reminder_workout_time": "18:00", "reminder_streak_time": "20:00", "reminder_sub_time": "12:00"}
        for k, v in defaults.items():
            await db.execute("INSERT OR IGNORE INTO settings (key, value) VALUES (?, ?)", (k, v))
        await db.commit()


async def get_setting(key, default=""):
    async with aiosqlite.connect(DB_FILE) as db:
        async with db.execute("SELECT value FROM settings WHERE key=?", (key,)) as cur:
            row = await cur.fetchone()
            return row[0] if row else default


def _parse(val, default):
    if not val: return default
    try: return json.loads(val)
    except Exception: return default


async def get_user_full(uid: str):
    async with aiosqlite.connect(DB_FILE) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute("SELECT * FROM users WHERE user_id = ?", (int(uid),)) as cur:
            row = await cur.fetchone()
            if not row: return None
            u = dict(row)
            u["profile"] = {
                "name": u.get("name", ""), "age": u.get("age", 16), "sex": u.get("sex") or "male",
                "weight": u.get("weight", 65), "height": u.get("height", 175), "activity": u.get("activity", 1.375),
                "goal": u.get("goal", "gain"), "kcal": u.get("kcal_target", 0),
                "protein": u.get("protein_target", 0), "fat": u.get("fat_target", 0), "carbs": u.get("carb_target", 0),
                "xp": u.get("xp", 0), "level": u.get("level", 1), "frame": u.get("frame", "none"),
                "reminders": _parse(u.get("reminders_json"), {"water": True, "workout": True})
            }
            u["foods"] = _parse(u.get("foods_json"), [])
            u["plans"] = _parse(u.get("plans_json"), {})
            u["todayWorkouts"] = _parse(u.get("today_workouts_json"), [])
            u["customWorkouts"] = _parse(u.get("custom_workouts_json"), [])
            u["streak"] = _parse(u.get("streak_json"), [False] * 7)
            u["waterToday"] = u.get("water_today") or 0
            u["sleepHistory"] = _parse(u.get("sleep_json"), {})
            u["weightHistory"] = _parse(u.get("weight_log_json"), [])
            u["achievements"] = _parse(u.get("achievements_json"), [])
            u["mealTemplates"] = _parse(u.get("meal_templates_json"), [])
            u["photos"] = _parse(u.get("photos_json"), [])
            u["friends"] = _parse(u.get("friends_json"), [])
            u["recentFoods"] = _parse(u.get("recent_foods_json"), [])
            u["dailyQuests"] = _parse(u.get("daily_quests_json"), {})
            u["foodLog"] = _parse(u.get("food_log_json"), {})
            u["sub_until"] = u.get("sub_until") or 0
            u["trial_used"] = u.get("trial_used") or 0
            return u


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
    if not uid: return web.json_response({"error": "No ID"}, status=400)
    now = int(time.time())

    async with aiosqlite.connect(DB_FILE) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute("SELECT sub_until FROM users WHERE user_id = ?", (int(uid),)) as cur:
            row = await cur.fetchone()
        if row and (not row["sub_until"] or row["sub_until"] < now):
            return web.json_response({"error": "Subscription expired"}, status=403)
        if not row:
            await db.execute("INSERT INTO users (user_id, name, sub_until, created_at, trial_used) VALUES (?, ?, ?, ?, ?)",
                (int(uid), "User", 0, datetime.utcnow().isoformat(), 0))

        if "profile" in data:
            p = data["profile"]
            await db.execute("""UPDATE users SET name=?, age=?, sex=?, weight=?, height=?, activity=?, goal=?, 
                   kcal_target=?, protein_target=?, fat_target=?, carb_target=?, xp=?, level=?, frame=?, reminders_json=?
                   WHERE user_id=?""",
                (p.get("name"), p.get("age"), p.get("sex") or p.get("gender"), p.get("weight"),
                 p.get("height"), p.get("activity"), p.get("goal"), p.get("kcal"),
                 p.get("protein", 0), p.get("fat", 0), p.get("carbs", 0),
                 p.get("xp", 0), p.get("level", 1), p.get("frame", "none"),
                 json.dumps(p.get("reminders", {"water": True, "workout": True})), int(uid)))

        field_map = {
            "foods": "foods_json", "plans": "plans_json", "todayWorkouts": "today_workouts_json",
            "customWorkouts": "custom_workouts_json", "streak": "streak_json", "waterToday": "water_today",
            "sleepHistory": "sleep_json", "weightHistory": "weight_log_json", "achievements": "achievements_json",
            "mealTemplates": "meal_templates_json", "photos": "photos_json", "friends": "friends_json",
            "recentFoods": "recent_foods_json", "dailyQuests": "daily_quests_json", "foodLog": "food_log_json"
        }
        for key, col in field_map.items():
            if key in data:
                val = data[key]
                if col == "water_today":
                    await db.execute(f"UPDATE users SET {col}=? WHERE user_id=?", (int(val or 0), int(uid)))
                else:
                    await db.execute(f"UPDATE users SET {col}=? WHERE user_id=?", (json.dumps(val, ensure_ascii=False), int(uid)))
        await db.commit()
    return web.json_response({"status": "ok"})

async def api_add_friend(request):
    data = await request.json()
    my_id = int(data.get("id"))
    target_username = data.get("username", "").replace("@", "").strip()
    
    async with aiosqlite.connect(DB_FILE) as db:
        db.row_factory = aiosqlite.Row
        # Ищем пользователя по юзернейму
        async with db.execute("SELECT user_id, name, streak_count FROM users WHERE username = ?", (target_username,)) as cur:
            target = await cur.fetchone()
        if not target:
            return web.json_response({"error": "Пользователь не найден. Попроси его зайти в бота."}, status=404)
        
        # Получаем список друзей
        async with db.execute("SELECT friends_json FROM users WHERE user_id=?", (my_id,)) as cur:
            my_row = await cur.fetchone()
            friends = _parse(my_row["friends_json"], [])
        
        if target["user_id"] in friends:
            return web.json_response({"error": "Уже в друзьях"}, status=400)
        
        friends.append(target["user_id"])
        await db.execute("UPDATE users SET friends_json=? WHERE user_id=?", (json.dumps(friends), my_id))
        await db.commit()
    return web.json_response({"status": "ok", "friend": {"id": target["user_id"], "name": target["name"]}})

async def api_leaderboard(request):
    my_id = int(request.query.get("id"))
    async with aiosqlite.connect(DB_FILE) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute("SELECT friends_json FROM users WHERE user_id=?", (my_id,)) as cur:
            row = await cur.fetchone()
            friends = _parse(row["friends_json"], []) if row else []
        
        all_ids = friends + [my_id]
        placeholders = ','.join('?' * len(all_ids))
        async with db.execute(f"SELECT user_id, name, streak_count, level, frame FROM users WHERE user_id IN ({placeholders})", all_ids) as cur:
            users = await cur.fetchall()
        
        result = []
        for u in users:
            result.append({
                "id": u["user_id"], "name": u["name"], "streak": u["streak_count"], 
                "level": u["level"], "frame": u["frame"], "is_me": u["user_id"] == my_id
            })
        result.sort(key=lambda x: x["streak"], reverse=True)
        return web.json_response(result)

async def api_send_poke(request):
    data = await request.json()
    my_id = int(data.get("id"))
    target_id = int(data.get("target_id"))
    poke_type = data.get("type", "fire") # fire / turtle
    
    async with aiosqlite.connect(DB_FILE) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute("SELECT name FROM users WHERE user_id=?", (my_id,)) as cur:
            me = await cur.fetchone()
        
    emoji = "🔥" if poke_type == "fire" else "🐢"
    text = f"{emoji} Тебе подкол от друга {me['name']}!" if me else f"{emoji} Тебе подкол от друга!"
    try:
        await bot.send_message(target_id, text)
        return web.json_response({"status": "ok"})
    except Exception:
        return web.json_response({"error": "Не удалось отправить"}, status=500)

async def api_exercise_history(request):
    uid = int(request.query.get("id"))
    ex_name = request.query.get("name", "")
    async with aiosqlite.connect(DB_FILE) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute("SELECT weight, reps, date FROM exercise_history WHERE user_id=? AND ex_name=? ORDER BY date DESC LIMIT 1", (uid, ex_name)) as cur:
            row = await cur.fetchone()
            return web.json_response(dict(row) if row else {})

async def api_exercise_log(request):
    data = await request.json()
    uid = int(data.get("id"))
    ex_name = data.get("name")
    weight = float(data.get("weight", 0))
    reps = int(data.get("reps", 0))
    today = datetime.now().strftime("%Y-%m-%d")
    async with aiosqlite.connect(DB_FILE) as db:
        await db.execute("INSERT INTO exercise_history (user_id, ex_name, weight, reps, date) VALUES (?, ?, ?, ?, ?)", (uid, ex_name, weight, reps, today))
        await db.commit()
    return web.json_response({"status": "ok"})

async def api_weight_add(request):
    data = await request.json()
    uid = int(data.get("id")); weight = float(data.get("weight"))
    today = datetime.now().strftime("%Y-%m-%d")
    async with aiosqlite.connect(DB_FILE) as db:
        await db.execute("INSERT INTO weight_log (user_id, date, weight, ts) VALUES (?, ?, ?, ?)", (uid, today, weight, datetime.now().isoformat()))
        await db.execute("UPDATE users SET weight = ? WHERE user_id = ?", (weight, uid))
        await db.commit()
    return web.json_response({"status": "ok"})

async def api_export_csv(request):
    # ... (оставил как было)
    uid = int(request.query.get("id")); type_ = request.query.get("type", "food")
    table = "food_log" if type_ == "food" else "workouts_log"
    async with aiosqlite.connect(DB_FILE) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(f"SELECT * FROM {table} WHERE user_id=?", (uid,)) as cur:
            rows = await cur.fetchall()
    output = io.StringIO()
    if rows:
        writer = csv.DictWriter(output, fieldnames=rows[0].keys())
        writer.writeheader()
        for r in rows: writer.writerow(dict(r))
    return web.Response(text=output.getvalue(), content_type="text/csv", headers={"Content-Disposition": f'attachment; filename="{type_}.csv"'})

async def api_export_pdf(request):
    # ... (оставил как было)
    uid = int(request.query.get("id"))
    buffer = io.BytesIO(); p = canvas.Canvas(buffer, pagesize=A4)
    p.drawString(100, 800, f"FitPlan Report — user {uid}")
    p.save(); buffer.seek(0)
    return web.Response(body=buffer.read(), content_type="application/pdf", headers={"Content-Disposition": 'attachment; filename="report.pdf"'})

async def start_web_server():
    app = web.Application(client_max_size=20 * 1024 * 1024)
    app.router.add_get("/", handle_index)
    app.router.add_get("/api/user", api_get_user)
    app.router.add_post("/api/save", api_save_all)
    app.router.add_post("/api/friends/add", api_add_friend)
    app.router.add_get("/api/friends/leaderboard", api_leaderboard)
    app.router.add_post("/api/friends/poke", api_send_poke)
    app.router.add_get("/api/exercise/history", api_exercise_history)
    app.router.add_post("/api/exercise/log", api_exercise_log)
    app.router.add_post("/api/weight/add", api_weight_add)
    app.router.add_get("/api/export/csv", api_export_csv)
    app.router.add_get("/api/export/pdf", api_export_pdf)
    app.router.add_get("/{filename}", handle_static)

    runner = web.AppRunner(app); await runner.setup()
    site = web.TCPSite(runner, "0.0.0.0", 3000); await site.start()
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
            await db.execute("INSERT INTO users (user_id, name, username, sub_until, created_at, trial_used) VALUES (?,?,?,?,?,?)",
                (uid, message.from_user.first_name, message.from_user.username, 0, datetime.utcnow().isoformat(), 0))
            await db.commit()
        else:
            # Обновляем юзернейм, если он изменился
            await db.execute("UPDATE users SET username=? WHERE user_id=?", (message.from_user.username, uid))
            await db.commit()
    await message.answer("Привет! Это <b>FitPlan</b>.\n\nТвой личный тренер и дневник питания.", reply_markup=get_main_menu())

# ... (Остальной код бота, админки и подписок оставляем без изменений, как в предыдущем ответе)

async def main():
    await init_db()
    await start_web_server()
    scheduler.start()
    print("🚀 Бот и сервер запущены")
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
