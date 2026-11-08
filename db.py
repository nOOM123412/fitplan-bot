import aiosqlite
import json
import os
import uuid
from datetime import datetime

DB_PATH = "/app/data/fitplan.db"
OLD_JSON_PATH = "/app/data/users.json"

async def init_db():
    os.makedirs("/app/data", exist_ok=True)
    async with aiosqlite.connect(DB_PATH) as db:
        # 1. Пользователи
        await db.execute("""
            CREATE TABLE IF NOT EXISTS users (
                user_id INTEGER PRIMARY KEY,
                username TEXT,
                name TEXT, age INTEGER, sex TEXT,
                weight REAL, height REAL, activity REAL, goal TEXT,
                kcal_target REAL, protein_target REAL, fat_target REAL, carb_target REAL,
                balance REAL DEFAULT 0,
                subscription_until TEXT,
                trial_used INTEGER DEFAULT 0,
                streak INTEGER DEFAULT 0,
                last_workout_date TEXT,
                timezone TEXT DEFAULT 'Europe/Moscow',
                created_at TEXT
            )
        """)
        # 2. Лог тренировок
        await db.execute("""
            CREATE TABLE IF NOT EXISTS workouts_log (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER, date TEXT, plan_id TEXT, plan_name TEXT,
                duration_min INTEGER, calories_burned REAL, exercises_json TEXT
            )
        """)
        # 3. Лог питания
        await db.execute("""
            CREATE TABLE IF NOT EXISTS food_log (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER, date TEXT, ts TEXT,
                name TEXT, grams REAL,
                kcal REAL, protein REAL, fat REAL, carb REAL
            )
        """)
        # 4. Пользовательские планы
        await db.execute("""
            CREATE TABLE IF NOT EXISTS custom_plans (
                id TEXT PRIMARY KEY, owner_id INTEGER, name TEXT, description TEXT,
                exercises_json TEXT, is_public INTEGER DEFAULT 1, created_at TEXT
            )
        """)
        await db.commit()
        
    # Миграция из старого users.json
    if os.path.exists(OLD_JSON_PATH):
        print("Найдены старые данные. Миграция в SQLite...")
        try:
            with open(OLD_JSON_PATH, "r", encoding="utf-8") as f:
                old_data = json.load(f)
            async with aiosqlite.connect(DB_PATH) as db:
                for uid, u in old_data.items():
                    await db.execute("""
                        INSERT OR IGNORE INTO users 
                        (user_id, username, name, age, sex, weight, height, activity, goal, 
                         kcal_target, protein_target, fat_target, carb_target, balance, 
                         subscription_until, trial_used, streak, last_workout_date, created_at)
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """, (
                        int(uid), u.get("username"), u.get("name"), u.get("age"), u.get("sex"),
                        u.get("weight"), u.get("height"), u.get("activity"), u.get("goal"),
                        u.get("kcal_target"), u.get("protein_target"), u.get("fat_target"), u.get("carb_target"),
                        u.get("balance", 0), u.get("subscription_until"), 1 if u.get("trial_used") else 0,
                        u.get("streak", 0), u.get("last_workout_date"), datetime.utcnow().isoformat()
                    ))
                await db.commit()
            os.rename(OLD_JSON_PATH, OLD_JSON_PATH + ".migrated")
            print("Миграция завершена.")
        except Exception as e:
            print(f"Ошибка миграции: {e}")

async def get_user(user_id: int):
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute("SELECT * FROM users WHERE user_id = ?", (user_id,)) as cursor:
            row = await cursor.fetchone()
            return dict(row) if row else None

async def log_food(user_id: int, name: str, grams: float, kcal: float, p: float, f: float, c: float):
    today = datetime.now().strftime("%Y-%m-%d")
    ts = datetime.now().isoformat()
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("""
            INSERT INTO food_log (user_id, date, ts, name, grams, kcal, protein, fat, carb)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (user_id, today, ts, name, grams, kcal, p, f, c))
        await db.commit()

async def log_workout(user_id: int, plan_id: str, plan_name: str, duration: int, kcal_burn: float, exercises: list):
    today = datetime.now().strftime("%Y-%m-%d")
    ex_json = json.dumps(exercises, ensure_ascii=False)
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("""
            INSERT INTO workouts_log (user_id, date, plan_id, plan_name, duration_min, calories_burned, exercises_json)
            VALUES (?, ?, ?, ?, ?, ?, ?)
        """, (user_id, today, plan_id, plan_name, duration, kcal_burn, ex_json))
        await db.commit()
