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
TOKEN = "8718686711:AAHX4NrsVn4CfhHhMJr6Z5hsuBdvIvdfjQ"
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
async def handle_index(request): return web.FileResponse('./public/index.html')
async def handle_static(request): return web.FileResponse(f"./public/{request.match_info['filename']}")

async def api_get_user(request):
    user_id = request.query.get('id')
    db = load_db()
    if user_id in db: return web.json_response(db[user_id])
    return web.json_response({"error": "User not found"}, status=404)

async def api_save_user(request):
    data = await request.json()
    user_id = str(data.get('id'))
    if not user_id: return web.json_response({"error": "No ID"}, status=400)
    db = load_db()
    if user_id not in db:
        db[user_id] = {
            "name": data.get("name", "User"),
            "balance": 0,
            "sub_until": 0,  # Пробного периода нет, пока пользователь не купит
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

# ================= БОТ =================
def get_main_menu():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="Открыть FitPlan 🏋️", web_app=WebAppInfo(url=WEBAPP_URL))],
        [InlineKeyboardButton(text="💳 Подписка", callback_data="menu_sub")],
        [InlineKeyboardButton(text="👥 Реферальная система", callback_data="menu_ref")]
    ])

@dp.message(Command("start"))
async def cmd_start(message: types.Message):
    await message.answer("Привет! Это <b>FitPlan</b>.\n\nТвой личный тренер и дневник питания.", reply_markup=get_main_menu())

@dp.callback_query(F.data == "menu_sub")
async def cb_sub_info(callback: CallbackQuery):
    db = load_db()
    uid = str(callback.from_user.id)
    balance = db.get(uid, {}).get("balance", 0)
    sub_until = db.get(uid, {}).get("sub_until", 0)
    
    now = int(time.time())
    status = "❌ Не активна"
    if sub_until > now:
        days = (sub_until - now) // 86400
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
    db = load_db()
    uid = str(callback.from_user.id)
    
    if uid not in db:
        return await callback.answer("Ошибка: пользователь не найден", show_alert=True)
    
    if db[uid]['balance'] < 499:
        return await callback.answer("Недостаточно средств на балансе!", show_alert=True)
    
    db[uid]['balance'] -= 499
    current_until = max(db[uid]['sub_until'], int(time.time()))
    db[uid]['sub_until'] = current_until + (30 * 24 * 60 * 60)
    save_db(db)
    
    await callback.answer("Подписка оплачена!", show_alert=True)
    await cb_sub_info(callback)

@dp.callback_query(F.data == "menu_ref")
async def cb_ref_info(callback: CallbackQuery):
    db = load_db()
    uid = str(callback.from_user.id)
    user_data = db.get(uid, {})
    ref_count = user_data.get("ref_count", 0)
    ref_earned = user_data.get("ref_earned", 0)
    bot_username = (await bot.get_me()).username
    ref_link = f"https://t.me/{bot_username}?start=ref_{uid}"
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

# ================= АДМИН =================
class AdminState(StatesGroup):
    waiting_for_balance_user = State()
    waiting_for_balance_amount = State()

@dp.message(Command("admin"))
async def cmd_admin(message: types.Message):
    if message.from_user.id != ADMIN_ID: return await message.answer("⛔ Нет доступа.")
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="💰 Начислить баланс", callback_data="admin_add_balance")],
    ])
    await message.answer("👑 <b>Админ-панель</b>\nВыбери действие:", reply_markup=kb)

@dp.callback_query(F.data == "admin_add_balance")
async def cb_admin_balance(callback: CallbackQuery, state: FSMContext):
    if callback.from_user.id != ADMIN_ID: return
    await callback.message.edit_text("Введи <b>ID пользователя</b> (в Telegram):")
    await state.set_state(AdminState.waiting_for_balance_user)

@dp.message(AdminState.waiting_for_balance_user)
async def process_admin_user(message: Message, state: FSMContext):
    if message.from_user.id != ADMIN_ID: return
    await state.update_data(target_id=message.text.strip())
    await message.answer("Введи <b>сумму</b> в рублях (например, 500):")
    await state.set_state(AdminState.waiting_for_balance_amount)

@dp.message(AdminState.waiting_for_balance_amount)
async def process_admin_amount(message: Message, state: FSMContext):
    if message.from_user.id != ADMIN_ID: return
    try:
        amount = int(message.text.strip())
        data = await state.get_data()
        target_id = data['target_id']
        db = load_db()
        if target_id in db:
            db[target_id]['balance'] += amount
            save_db(db)
            await message.answer(f"✅ Баланс <code>{target_id}</code> пополнен на <b>{amount} ₽</b>.")
            try: await bot.send_message(target_id, f"🎉 Твой баланс пополнен на <b>{amount} ₽</b>!")
            except: pass
        else: await message.answer("❌ Пользователь не найден.")
    except ValueError: await message.answer("❌ Введи число.")
    finally: await state.clear()

# ================= ЗАПУСК =================
async def main():
    await start_web_server()
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
