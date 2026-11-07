import asyncio
import logging
from aiogram import Bot, Dispatcher, types, F
from aiogram.filters import Command
from aiogram.types import WebAppInfo, InlineKeyboardMarkup, InlineKeyboardButton, LabeledPrice, PreCheckoutQuery
from aiogram.client.default import DefaultBotProperties

# Твой токен от @BotFather
TOKEN = "ТВОЙ_ТОКЕН_ЗДЕСЬ"
# Ссылка на твой index.html (загрузи его на GitHub Pages или Vercel)
WEBAPP_URL = "https://твой-сайт.github.io/fitplan/index.html"

logging.basicConfig(level=logging.INFO)
bot = Bot(token=TOKEN, default=DefaultBotProperties(parse_mode="HTML"))
dp = Dispatcher()

@dp.message(Command("start"))
async def cmd_start(message: types.Message):
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="Открыть FitPlan 🏋️", web_app=WebAppInfo(url=WEBAPP_URL))]
    ])
    await message.answer(
        "Привет! Это <b>FitPlan</b>.\n\n"
        "Твой личный тренер и дневник питания. Считай КБЖУ, выбирай планы тренировок и следи за стриками.",
        reply_markup=kb
    )

# Обработка платежей (Telegram Stars)
@dp.message(Command("pay"))
async def cmd_pay(message: types.Message):
    # 50 звезд за подписку
    await bot.send_invoice(
        chat_id=message.chat.id,
        title="FitPlan Premium",
        description="Доступ к расширенным планам и закрытому чату",
        payload="premium_subscription_1month",
        provider_token="", # Для звезд оставляем пустым
        currency="XTR", # Telegram Stars
        prices=[LabeledPrice(label="Premium", amount=50)]
    )

@dp.pre_checkout_query()
async def pre_checkout(pre_checkout_q: PreCheckoutQuery):
    await bot.answer_pre_checkout_query(pre_checkout_q.id, ok=True)

@dp.message(F.successful_payment)
async def successful_payment(message: types.Message):
    await message.answer(f"Оплата прошла успешно! Спасибо за поддержку 🚀")

async def main():
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())