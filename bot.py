import os
import json
import time
import asyncio
import logging
from aiogram import Bot, Dispatcher, types, F
from aiogram.filters import Command
from aiogram.types import WebAppInfo, InlineKeyboardMarkup, InlineKeyboardButton, Message
from aiogram.client.default import DefaultBotProperties
from aiohttp import web
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.context import FSMContext
from aiogram.fsm.storage.memory import MemoryStorage

# ================= НАСТРОЙКИ =================
TOKEN = "-"
WEBAPP_URL = "https://bot-1794065452-4003-noom.bothost.tech"
ADMIN_ID = 7714111634
OPERATOR = "@sai_helper"

logging.basicConfig(level=logging.INFO)
bot = Bot(token=TOKEN, default=DefaultBotProperties(parse_mode="HTML"))
dp = Dispatcher(storage=MemoryStorage())

# ================= БАЗА ДАННЫХ =================
# BotHost сохраняет файлы в /app/data, чтобы они не стирались при перезапуске
DATA_DIR = os.environ.get('DATA_DIR', '/app/data')
os.makedirs(DATA_DIR, exist_ok=True)
DB_FILE = os.path.join(DATA_DIR, 'users.json')

def load_db():
    if not os.path.exists(DB_FILE):
        return {}
    try:
        with open(DB_FILE, 'r', encoding='utf-8') as f:
            return json.load(f)
    except:
        return {}

def save_db(data):
    with open(DB_FILE, 'w', encoding='utf-8') as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

# ================= API ВЕБ-СЕРВЕРА =================
async def handle_index(request):
    return web.FileResponse('./public/index.html')

async def handle_static(request):
    filename = request.match_info['filename']
    return web.FileResponse(f'./public/{filename}')

async def api_get_user(request):
    user_id = request.query.get('id')
    db = load_db()
    if user_id in db:
        return web.json_response(db[user_id])
    return web.json_response({"error": "User not found"}, status=404)

async def api_save_user(request):
    data = await request.json()
    user_id = str(data.get('id'))
    if not user_id:
        return web.json_response({"error": "No ID"}, status=400)
    
    db = load_db()
    if user_id not in db:
        db[user_id] = {
            "name": data.get("name", "User"),
            "balance": 0,
            "sub_until": int(time.time()) + (3 * 24 * 60 * 60), # +3 дня пробного
            "referred_by": data.get("referred_by"),
            "ref_count": 0,
            "ref_earned": 0
        }
    else:
        db[user_id]['name'] = data.get('name', db[user_id]['name'])
        
    save_db(db)
    return web.json_response({"status": "ok"})

async def api_buy_sub(request):
    data = await request.json()
    user_id = str(data.get('id'))
    db = load_db()
    
    if user_id in db:
        if db[user_id]['balance'] >= 499:
            db[user_id]['balance'] -= 499
            current_until = max(db[user_id]['sub_until'], int(time.time()))
            db[user_id]['sub_until'] = current_until + (30 * 24 * 60 * 60)
            save_db(db)
            return web.json_response({"status": "ok", "new_balance": db[user_id]['balance'], "sub_until": db[user_id]['sub_until']})
        else:
            return web.json_response({"error": "Not enough money"}, status=400)
    return web.json_response({"error": "User not found"}, status=404)

async def start_web_server():
    app = web.Application()
    app.router.add_get('/', handle_index)
    app.router.add_get('/api/user', api_get_user)
    app.router.add_post('/api/save', api_save_user)
    app.router.add_post('/api/buy_sub', api_buy_sub)
    app.router.add_get('/{filename}', handle_static)
    
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, '0.0.0.0', 3000)
    await site.start()
    logging.info("✅ Веб-сервер запущен на порту 3000")

# ================= ПОЛЬЗОВАТЕЛЬСКАЯ ЧАСТЬ =================
@dp.message(Command("start"))
async def cmd_start(message: types.Message):
    args = message.text.split()
    ref_id = None
    if len(args) > 1 and args[1].startswith('ref_'):
        ref_id = args[1].replace('ref_', '')

    app_url = f"{WEBAPP_URL}?ref={ref_id}" if ref_id else WEBAPP_URL

    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="Открыть FitPlan 🏋️", web_app=WebAppInfo(url=app_url))],
        [InlineKeyboardButton(text="💳 Подписка", callback_data="sub_info")],
        [InlineKeyboardButton(text="👥 Реферальная система", callback_data="ref_info")]
    ])
    await message.answer(
        "Привет! Это <b>FitPlan</b>.\n\n"
        "Твой личный тренер и дневник питания. У тебя есть <b>3 дня бесплатного доступа</b>!",
        reply_markup=kb
    )

@dp.callback_query(F.data == "sub_info")
async def cb_sub_info(callback: types.CallbackQuery):
    db = load_db()
    uid = str(callback.from_user.id)
    user_data = db.get(uid, {})
    balance = user_data.get("balance", 0)
    
    text = (
        f"💳 <b>Твоя подписка</b>\n\n"
        f"Баланс: <b>{balance} ₽</b>\n\n"
        f"Стоимость подписки на 1 месяц:\n"
        f"<s>999 ₽</s> <b>499 ₽</b> (-50% 🎉)\n\n"
        f"Для пополнения баланса напиши нашему оператору: {OPERATOR}"
    )
    await callback.message.answer(text)

@dp.callback_query(F.data == "ref_info")
async def cb_ref_info(callback: types.CallbackQuery):
    db = load_db()
    uid = str(callback.from_user.id)
    user_data = db.get(uid, {})
    ref_count = user_data.get("ref_count", 0)
    ref_earned = user_data.get("ref_earned", 0)
    
    bot_username = (await bot.get_me()).username
    ref_link = f"https://t.me/{bot_username}?start=ref_{uid}"
    
    text = (
        f"👥 <b>Реферальная система</b>\n\n"
        f"Приглашай друзей и получай <b>10%</b> с каждого их пополнения!\n\n"
        f"Твоя ссылка:\n<code>{ref_link}</code>\n\n"
        f"Приглашено друзей: <b>{ref_count}</b>\n"
        f"Заработано: <b>{ref_earned} ₽</b>"
    )
    await callback.message.answer(text)

# ================= АДМИН-ПАНЕЛЬ =================
class AdminState(StatesGroup):
    waiting_for_balance_user = State()
    waiting_for_balance_amount = State()

@dp.message(Command("admin"))
async def cmd_admin(message: types.Message):
    if message.from_user.id != ADMIN_ID:
        return await message.answer("⛔ У вас нет доступа.")
    
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="💰 Начислить баланс", callback_data="admin_add_balance")],
        [InlineKeyboardButton(text="👑 Выдать подписку", callback_data="admin_give_sub")]
    ])
    await message.answer("👑 <b>Админ-панель</b>\nВыбери действие:", reply_markup=kb)

@dp.callback_query(F.data == "admin_add_balance")
async def cb_admin_add_balance(callback: types.CallbackQuery, state: FSMContext):
    if callback.from_user.id != ADMIN_ID: return
    await callback.message.answer("Введи <b>ID пользователя</b> (в Telegram):")
    await state.set_state(AdminState.waiting_for_balance_user)

@dp.message(AdminState.waiting_for_balance_user)
async def process_admin_balance_user(message: types.Message, state: FSMContext):
    if message.from_user.id != ADMIN_ID: return
    await state.update_data(target_id=message.text.strip())
    await message.answer("Введи <b>сумму</b> в рублях (например, 500):")
    await state.set_state(AdminState.waiting_for_balance_amount)

@dp.message(AdminState.waiting_for_balance_amount)
async def process_admin_balance_amount(message: types.Message, state: FSMContext):
    if message.from_user.id != ADMIN_ID: return
    try:
        amount = int(message.text.strip())
        data = await state.get_data()
        target_id = data['target_id']
        
        db = load_db()
        if target_id in db:
            db[target_id]['balance'] += amount
            save_db(db)
            await message.answer(f"✅ Баланс пользователя <code>{target_id}</code> пополнен на <b>{amount} ₽</b>.\nТекущий баланс: {db[target_id]['balance']} ₽")
            try:
                await bot.send_message(target_id, f"🎉 Твой баланс пополнен на <b>{amount} ₽</b>!\nСпасибо за поддержку!")
            except:
                pass
        else:
            await message.answer("❌ Пользователь не найден в базе.")
    except ValueError:
        await message.answer("❌ Введи число.")
    finally:
        await state.clear()

@dp.callback_query(F.data == "admin_give_sub")
async def cb_admin_give_sub(callback: types.CallbackQuery):
    if callback.from_user.id != ADMIN_ID: return
    await callback.message.answer("Эта функция в разработке. Используй начисление баланса, чтобы пользователь купил подписку сам.")

# ================= ЗАПУСК =================
async def main():
    await start_web_server()
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
