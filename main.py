import asyncio
import json
import os
import time
import random
import logging
import aiohttp
from aiogram import Bot, Dispatcher, types, F
from aiogram.filters import Command
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton, LabeledPrice, PreCheckoutQuery
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import StatesGroup, State
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.client.default import DefaultBotProperties
import aiosqlite

TOKEN = os.getenv("TOKEN")
CRYPTOBOT_TOKEN = os.getenv("CRYPTOBOT_TOKEN")

# Дебаг — проверяем токены
print(f"DEBUG: TOKEN type = {type(TOKEN)}, value = {TOKEN[:10]}..." if TOKEN else "TOKEN = None")
print(f"DEBUG: CRYPTOBOT_TOKEN type = {type(CRYPTOBOT_TOKEN)}, value = {CRYPTOBOT_TOKEN[:10]}..." if CRYPTOBOT_TOKEN else "CRYPTOBOT_TOKEN = None")

bot = Bot(token=TOKEN, default=DefaultBotProperties(parse_mode="HTML"))
storage = MemoryStorage()
dp = Dispatcher(storage=storage)
DB = "/tmp/dating.db"  # Persistent on Render free tier (docs: /tmp survives sleep)

async def init_db():
    async with aiosqlite.connect(DB) as db:
        await db.executescript('''
            CREATE TABLE IF NOT EXISTS users (
                user_id INTEGER PRIMARY KEY, name TEXT, age INTEGER, city TEXT, gender TEXT,
                looking_for TEXT, bio TEXT, photos TEXT, vip_until INTEGER DEFAULT 0, registered_at INTEGER
            );
            CREATE TABLE IF NOT EXISTS likes (from_user INTEGER, to_user INTEGER, PRIMARY KEY (from_user, to_user));
            CREATE TABLE IF NOT EXISTS boosts (user_id INTEGER PRIMARY KEY, until INTEGER);
        ''')
        await db.commit()
    print("База создана OK")

def main_menu():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="👤 Моя анкета", callback_data="profile")],
        [InlineKeyboardButton(text="🔍 Поиск", callback_data="search")],
        [InlineKeyboardButton(text="💎 VIP и поднятия", callback_data="payments")]
    ])

class Profile(StatesGroup):
    name = State(); age = State(); city = State(); gender = State()
    looking = State(); bio = State(); photo = State()

@dp.message(Command("start"))
async def start(msg: types.Message):
    print(f"Получил /start от {msg.from_user.id}")
    async with aiosqlite.connect(DB) as db:
        await db.execute("INSERT OR IGNORE INTO users (user_id, registered_at) VALUES (?, ?)",
                        (msg.from_user.id, int(time.time())))
        await db.commit()
    await msg.answer("❤️ Добро пожаловать в анонимные знакомства!\nСоздай анкету ↓", reply_markup=main_menu())
    print("Ответ на /start отправлен")

@dp.callback_query(F.data == "profile")
async def profile(call: types.CallbackQuery, state: FSMContext):
    print(f"Клик по profile от {call.from_user.id}")
    async with aiosqlite.connect(DB) as db:
        async with db.execute("SELECT name FROM users WHERE user_id = ?", (call.from_user.id,)) as cursor:
            row = await cursor.fetchone()
    if row and row[0]:
        await show_my_profile(call.message, call.from_user.id)
    else:
        await call.message.answer("📝 Создаём анкету!\nНапиши имя (можно фейковое)")
        await state.set_state(Profile.name)

async def show_my_profile(message: types.Message, user_id: int):
    async with aiosqlite.connect(DB) as db:
        async with db.execute("SELECT name, age, city, bio, photos FROM users WHERE user_id = ?", (user_id,)) as cursor:
            row = await cursor.fetchone()
    if not row:
        return
    photos = json.loads(row[4]) if row[4] else []
    text = f"👤 <b>{row[0]}, {row[1]}</b>\n🏙 {row[2]}\n\n{row[3] or 'Без описания'}"
    if photos:
        await message.answer_photo(photos[0], caption=text, reply_markup=main_menu())
    else:
        await message.answer(text, reply_markup=main_menu())

@dp.message(Profile.name)
async def p_name(msg: types.Message, state: FSMContext):
    print(f"Имя от {msg.from_user.id}: {msg.text}")
    await state.update_data(name=msg.text[:30])
    await msg.answer("🎂 Возраст?")
    await state.set_state(Profile.age)

@dp.message(Profile.age)
async def p_age(msg: types.Message, state: FSMContext):
    if not msg.text.isdigit() or not 14 <= int(msg.text) <= 80:
        await msg.answer("Возраст 14–80")
        return
    await state.update_data(age=int(msg.text))
    await msg.answer("🏙 Город?")
    await state.set_state(Profile.city)

@dp.message(Profile.city)
async def p_city(msg: types.Message, state: FSMContext):
    await state.update_data(city=msg.text[:50])
    await msg.answer("🚹 Пол?", reply_markup=gender_kb())
    await state.set_state(Profile.gender)

@dp.callback_query(F.data.startswith("gender_"))
async def p_gender(call: types.CallbackQuery, state: FSMContext):
    await state.update_data(gender=call.data.split("_")[1])
    await call.message.answer("❤️ Кого ищешь?")
    await state.set_state(Profile.looking)

@dp.message(Profile.looking)
async def p_looking(msg: types.Message, state: FSMContext):
    await state.update_data(looking=msg.text)
    kb = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="Пропустить", callback_data="skip_bio")]])
    await msg.answer("✍️ О себе?", reply_markup=kb)
    await state.set_state(Profile.bio)

@dp.callback_query(F.data == "skip_bio")
async def skip_bio(call: types.CallbackQuery, state: FSMContext):
    await state.update_data(bio="")
    kb = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="Готово", callback_data="done")]])
    await call.message.answer("📸 Отправь 1–5 фото", reply_markup=kb)
    await state.update_data(photos=[])
    await state.set_state(Profile.photo)

@dp.message(Profile.bio)
async def p_bio(msg: types.Message, state: FSMContext):
    await state.update_data(bio=msg.text[:300])
    await skip_bio(msg, state)

@dp.message(Profile.photo, F.photo)
async def p_photo(msg: types.Message, state: FSMContext):
    data = await state.get_data()
    photos = data.get("photos", [])
    if len(photos) >= 5:
        await msg.answer("Макс 5 фото")
        return
    photos.append(msg.photo[-1].file_id)
    await state.update_data(photos=photos)
    await msg.answer(f"Фото добавлено ({len(photos)}/5)")

@dp.callback_query(F.data == "done")
async def done(call: types.CallbackQuery, state: FSMContext):
    data = await state.get_data()
    photos = json.dumps(data.get("photos", []))
    async with aiosqlite.connect(DB) as db:
        await db.execute("""INSERT OR REPLACE INTO users 
            (user_id, name, age, city, gender, looking_for, bio, photos, registered_at)
            VALUES (?,?,?,?,?,?,?,?,?)""",
            (call.from_user.id, data["name"], data["age"], data["city"], data["gender"],
             data["looking"], data.get("bio",""), photos, int(time.time())))
        await db.commit()
    await call.message.answer("🎉 Анкета готова! Ищи матчи ❤️", reply_markup=main_menu())
    await state.clear()

# ==================== ПОИСК ====================
@dp.callback_query(F.data == "search")
async def search(call: types.CallbackQuery):
    prof = await get_random_profile(call.from_user.id)
    if not prof:
        await call.message.answer("😔 Анкет мало. Приглашай друзей!")
        return
    photos = json.loads(prof[5])
    text = f"<b>{prof[1]}, {prof[2]}</b>\n🏙 {prof[3]}\n\n{prof[4] or ''}"
    await call.message.answer_photo(photos[0], caption=text, reply_markup=like_kb(prof[0]))

async def get_random_profile(user_id: int):
    async with aiosqlite.connect(DB) as db:
        async with db.execute("SELECT user_id, name, age, city, bio, photos FROM users WHERE user_id != ? AND photos != '[]' ORDER BY RANDOM() LIMIT 30", (user_id,)) as cursor:
            rows = await cursor.fetchall()
    return random.choice(rows) if rows else None

@dp.callback_query(F.data.startswith("like_"))
async def like(call: types.CallbackQuery):
    to_id = int(call.data.split("_")[1])
    from_id = call.from_user.id
    async with aiosqlite.connect(DB) as db:
        await db.execute("INSERT OR IGNORE INTO likes VALUES (?,?)", (from_id, to_id))
        await db.commit()
        async with db.execute("SELECT 1 FROM likes WHERE from_user=? AND to_user=?", (to_id, from_id)) as cursor:
            if await cursor.fetchone():
                await bot.send_message(from_id, "❤️ Взаимный лайк! Чат открыт 🔥")
                await bot.send_message(to_id, "❤️ Взаимный лайк! Чат открыт 🔥")
    await call.answer("❤️")
    await search(call)

@dp.callback_query(F.data == "next")
async def next_profile(call: types.CallbackQuery):
    await search(call)

# ==================== ПЛАТЕЖИ ====================
@dp.callback_query(F.data == "payments")
async def payments(call: types.CallbackQuery):
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="Поднятие 24ч — 299 ₽", callback_data="pay_boost")],
        [InlineKeyboardButton(text="VIP 7 дней — 399 ₽", callback_data="pay_vip7")],
        [InlineKeyboardButton(text="VIP 30 дней — 699 ₽", callback_data="pay_vip30")]
    ])
    await call.message.answer("Платные функции", reply_markup=kb)

@dp.callback_query(F.data.startswith("pay_"))
async def pay(call: types.CallbackQuery):
    prices = {
        "pay_boost": [LabeledPrice("Поднятие 24ч", 29900)],
        "pay_vip7": [LabeledPrice("VIP 7 дней", 39900)],
        "pay_vip30": [LabeledPrice("VIP 30 дней", 69900)]
    }
    item = call.data
    await bot.send_invoice(
        chat_id=call.from_user.id,
        title="Функция",
        description=prices[item][0].label,
        payload=item,
        provider_token=CRYPTOBOT_TOKEN,
        currency="RUB",
        prices=prices[item],
        start_parameter="dating"
    )

@dp.pre_checkout_query()
async def pre_checkout(pcq: PreCheckoutQuery):
    await bot.answer_pre_checkout_query(pcq.id, ok=True)

@dp.message(F.successful_payment)
async def success(msg: types.Message):
    payload = msg.successful_payment.invoice_payload
    t = int(time.time())
    async with aiosqlite.connect(DB) as db:
        if "boost" in payload:
            await db.execute("INSERT OR REPLACE INTO boosts VALUES (?,?)", (msg.from_user.id, t + 86400))
        else:
            days = 7 if "vip7" in payload else 30
            await db.execute("UPDATE users SET vip_until = ? WHERE user_id = ?", (t + days*86400, msg.from_user.id))
        await db.commit()
    await msg.answer("Оплата прошла! Функция активирована 💎")

# ==================== ЗАПУСК ====================
async def main():
    await init_db()
    print("Бот запущен на Render 24/7!")
    await dp.start_polling(bot, polling_timeout=60)

if __name__ == "__main__":
    asyncio.run(main())
