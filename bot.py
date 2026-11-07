import os
import json
import asyncio
import logging
from aiogram import Bot, Dispatcher, types, F
from aiogram.filters import Command
from aiogram.types import WebAppInfo, InlineKeyboardMarkup, InlineKeyboardButton, LabeledPrice, PreCheckoutQuery
from aiogram.client.default import DefaultBotProperties
from aiohttp import web

# ================= НАСТРОЙКИ =================
TOKEN = "-"
WEBAPP_URL = "https://bot-1794065452-4003-noom.bothost.tech"
ADMIN_ID = 123456789  # Замени на свой Telegram ID (узнать можно у @userinfobot)
OPERATOR = "@sai_helper"

logging.basicConfig(level=logging.INFO)
bot = Bot(token=TOKEN, default=DefaultBotProperties(parse_mode="HTML"))
dp = Dispatcher()

DB_FILE = 'users.json'

def load_db():
    if not os.path.exists(DB_FILE):
        return {}
    with open(DB_FILE, 'r', encoding='utf-8') as f:
        return json.load(f)

def save_db(data):
    with open(DB_FILE, 'w', encoding='utf-8') as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

# ================= ВЕБ-СЕРВЕР (API) =================
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
        # Новый пользователь: даем 3 дня пробной подписки
        import time
        db[user_id] = {
            "name": data.get("name", "User"),
            "balance": 0,
            "sub_until": int(time.time()) + (3 * 24 * 60 * 60), # +3 дня
            "referred_by": data.get("referred_by"),
            "ref_count": 0,
            "ref_earned": 0
        }
    else:
        db[user_id]['name'] = data.get('name', db[user_id]['name'])
        
    save_db(db)
    return web.json_response({"status": "ok"})

async def api_add_balance(request):
    data = await request.json()
    user_id = str(data.get('id'))
    amount = data.get('amount', 0)
    db = load_db()
    
    if user_id in db:
        db[user_id]['balance'] += amount
        
        # Реферальная система: начисляем 10% тому, кто пригласил
        ref_by = db[user_id].get('referred_by')
        if ref_by and ref_by in db:
            bonus = int(amount * 0.10)
            db[ref_by]['balance'] += bonus
            db[ref_by]['ref_earned'] += bonus
            db[ref_by]['ref_count'] += 1
            
        save_db(db)
        return web.json_response({"status": "ok", "new_balance": db[user_id]['balance']})
    return web.json_response({"error": "User not found"}, status=404)

async def api_buy_sub(request):
    data = await request.json()
    user_id = str(data.get('id'))
    db = load_db()
    
    if user_id in db:
        if db[user_id]['balance'] >= 499:
            db[user_id]['balance'] -= 499
            import time
            # Если подписка еще активна, добавляем 30 дней к текущей, иначе от сейчас
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
    app.router.add_post('/api/add_balance', api_add_balance)
    app.router.add_post('/api/buy_sub', api_buy_sub)
    app.router.add_get('/{filename}', handle_static)
    
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, '0.0.0.0', 3000)
    await site.start()
    logging.info("✅ Веб-сервер запущен на порту 3000")

# ================= БОТ =================
@dp.message(Command("start"))
async def cmd_start(message: types.Message):
    # Парсим реферальный код из команды /start ref_12345
    args = message.text.split()
    ref_id = None
    if len(args) > 1 and args[1].startswith('ref_'):
        ref_id = args[1].replace('ref_', '')

    # Ссылка на Mini App с параметром startapp
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
    balance = db.get(uid, {}).get("balance", 0)
    
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

# ================= ЗАПУСК =================
async def main():
    await start_web_server()
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
