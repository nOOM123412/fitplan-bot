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
TOKEN = "-"
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
    uid = str(message.from_user.id)
    db = load_db()
    if uid not in db:
        db[uid] = {"name": message.from_user.first_name or "User", "tg_username": message.from_user.username or "", "balance": 0, "sub_until": 0, "referred_by": None, "ref_count": 0, "ref_earned": 0, "foods": [], "plans": {}, "streak": [False]*7, "profile": {}}
        save_db(db)
    await message.answer("Привет! Это <b>FitPlan</b>.\n\nТвой личный тренер и дневник питания.", reply_markup=get_main_menu())

@dp.callback_query(F.data == "menu_sub")
async def cb_sub_info(callback: CallbackQuery):
    db = load_db()
    uid = str(callback.from_user.id)
    balance = db.get(uid, {}).get("balance", 0)
    sub_until = db.get(uid, {}).get("sub_until", 0)
    now = int(time.time())
    status = "❌ Не активна"
    if sub_until > now: status = f"✅ Активна ({(sub_until - now) // 86400} дн.)"
    text = f"💳 <b>Твоя подписка</b>\n\nБаланс: <b>{balance} ₽</b>\nСтатус: <b>{status}</b>\n\nСтоимость подписки на 1 месяц:\n<s>999 ₽</s> <b>499 ₽</b> (-50% 🎉)\n\n"
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="💳 Оплатить с баланса", callback_data="pay_sub")],
        [InlineKeyboardButton(text="👨‍💻 Оператор", url=f"https://t.me/{OPERATOR.replace('@','')}")],
        [InlineKeyboardButton(text="⬅️ Назад", callback_data="menu_main")]
    ])
    await callback.message.edit_text(text, reply_markup=kb)

@dp.callback_query(F.data == "pay_sub")
async def cb_pay_sub(callback: CallbackQuery):
    db = load_db(); uid = str(callback.from_user.id)
    if uid not in db: return await callback.answer("Ошибка: пользователь не найден", show_alert=True)
    if db[uid].get('balance', 0) < 499: return await callback.answer("Недостаточно средств!", show_alert=True)
    db[uid]['balance'] -= 499
    db[uid]['sub_until'] = max(db[uid].get('sub_until', 0), int(time.time())) + (30 * 24 * 60 * 60)
    save_db(db)
    await callback.answer("Подписка оплачена!", show_alert=True)
    await cb_sub_info(callback)

@dp.callback_query(F.data == "menu_ref")
async def cb_ref_info(callback: CallbackQuery):
    db = load_db(); uid = str(callback.from_user.id)
    user_data = db.get(uid, {})
    bot_username = (await bot.get_me()).username
    text = f"👥 <b>Реферальная система</b>\n\nПриглашай друзей и получай <b>10%</b>!\n\nТвоя ссылка:\n<code>https://t.me/{bot_username}?start=ref_{uid}</code>\n\nПриглашено: <b>{user_data.get('ref_count', 0)}</b>\nЗаработано: <b>{user_data.get('ref_earned', 0)} ₽</b>"
    kb = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="⬅️ Назад", callback_data="menu_main")]])
    await callback.message.edit_text(text, reply_markup=kb)

@dp.callback_query(F.data == "menu_main")
async def cb_main_menu(callback: CallbackQuery):
    await callback.message.edit_text("Привет! Это <b>FitPlan</b>.\n\nТвой личный тренер и дневник питания.", reply_markup=get_main_menu())

# ================= АДМИН =================
class AdminState(StatesGroup):
    waiting_for_balance_user = State()
    waiting_for_balance_amount = State()

def get_admin_menu():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="💰 Начислить баланс", callback_data="admin_add_balance")],
        [InlineKeyboardButton(text="📋 Все пользователи", callback_data="admin_users_list")]
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
    db = load_db()
    if target_id not in db:
        await message.answer("❌ Пользователь не найден.")
        return await state.clear()
    await state.update_data(target_id=target_id)
    await message.answer(f"Введи <b>сумму</b> для <code>{db[target_id].get('name', 'User')}</code>:")
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
            ref_by = db[target_id].get('referred_by')
            if ref_by and ref_by in db:
                bonus = int(amount * 0.10)
                db[ref_by]['balance'] += bonus
                db[ref_by]['ref_earned'] += bonus
                db[ref_by]['ref_count'] += 1
                try: await bot.send_message(ref_by, f"🎉 Друг пополнил баланс! Тебе +{bonus} ₽.")
                except: pass
            save_db(db)
            try: await bot.send_message(target_id, f"🎉 Твой баланс пополнен на {amount} ₽!")
            except: pass
            await message.answer(f"✅ Баланс пополнен на {amount} ₽.")
            await cmd_admin(message)
    except ValueError: await message.answer("❌ Введи число.")
    finally: await state.clear()

@dp.callback_query(F.data == "admin_users_list")
async def cb_admin_users_list(callback: CallbackQuery):
    if callback.from_user.id != ADMIN_ID: return
    db = load_db()
    if not db: return await callback.message.edit_text("База пуста.")
    text = "📋 <b>Пользователи:</b>\n\n"
    for uid, data in list(db.items())[:30]:
        un = f" (@{data.get('tg_username', '')})" if data.get('tg_username') else ""
        text += f"👤 <b>{data.get('name', 'User')}</b>{un}\n🆔 <code>{uid}</code> | 💰 {data.get('balance', 0)} ₽\n\n"
    kb = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="⬅️ Назад", callback_data="admin_back")]])
    await callback.message.edit_text(text, reply_markup=kb)

@dp.callback_query(F.data == "admin_back")
async def cb_admin_back(callback: CallbackQuery):
    if callback.from_user.id != ADMIN_ID: return
    await callback.message.edit_text("👑 <b>Админ-панель</b>", reply_markup=get_admin_menu())

async def main():
    await start_web_server()
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
