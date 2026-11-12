
import os
import asyncio
import sqlite3
from datetime import datetime
from decimal import Decimal, InvalidOperation

from aiogram import Bot, Dispatcher, F
from aiogram.filters import Command
from aiogram.types import Message, ReplyKeyboardMarkup, KeyboardButton
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.context import FSMContext
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode


# НАСТРОЙКИ
BOT_TOKEN = os.getenv("BOT_TOKEN")
OWNER_ID = os.getenv("OWNER_ID")

if not BOT_TOKEN or not OWNER_ID:
    raise RuntimeError("Не заданы BOT_TOKEN и OWNER_ID")

OWNER_ID = int(OWNER_ID)

DB_NAME = "finance.db"

# БАЗА ДАННЫХ
def init_db():
    with sqlite3.connect(DB_NAME) as db:
        db.execute("""
            CREATE TABLE IF NOT EXISTS operations (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                owner_id INTEGER NOT NULL,
                type TEXT NOT NULL,
                amount INTEGER NOT NULL,
                category TEXT NOT NULL,
                comment TEXT,
                created_at TEXT NOT NULL
            )
        """)


# МЕНЮ
menu = ReplyKeyboardMarkup(
    keyboard=[
        [KeyboardButton(text="➕ Доход"), KeyboardButton(text="➖ Расход")],
        [KeyboardButton(text="💰 Баланс"), KeyboardButton(text="📜 История")],
        [KeyboardButton(text="📊 Статистика")]
    ],
    resize_keyboard=True
)


# СОСТОЯНИЯ
class FinanceStates(StatesGroup):
    amount = State()
    category = State()
    comment = State()


# ПРОВЕРКА ВЛАДЕЛЬЦА
def is_owner(message: Message):
    return message.from_user and message.from_user.id == OWNER_ID


def money(cents):
    return f"{Decimal(cents) / 100:,.2f}".replace(",", " ").replace(".", ",") + " ₽"


def get_balance():
    with sqlite3.connect(DB_NAME) as db:
        row = db.execute("""
            SELECT COALESCE(SUM(
                CASE WHEN type = 'income' THEN amount ELSE -amount END
            ), 0)
            FROM operations
            WHERE owner_id = ?
        """, (OWNER_ID,)).fetchone()
    return row[0]


# ЗАПУСК
dp = Dispatcher(storage=MemoryStorage())


@dp.message(Command("start"))
async def start(message: Message):
    if not is_owner(message):
        return

    await message.answer(
        "💰 <b>Мои Финансы</b>\n\n"
        "Личный финансовый помощник запущен!\n"
        "Выбирай действие в меню.",
        reply_markup=menu
    )


@dp.message(Command("cancel"))
async def cancel(message: Message, state: FSMContext):
    if not is_owner(message):
        return
    await state.clear()
    await message.answer("Операция отменена.", reply_markup=menu)


# НАЧАЛО ДОХОДА
@dp.message(F.text == "➕ Доход")
async def income_start(message: Message, state: FSMContext):
    if not is_owner(message):
        return
    await state.update_data(operation_type="income")
    await state.set_state(FinanceStates.amount)
    await message.answer("Введи сумму дохода в рублях:")


# НАЧАЛО РАСХОДА
@dp.message(F.text == "➖ Расход")
async def expense_start(message: Message, state: FSMContext):
    if not is_owner(message):
        return
    await state.update_data(operation_type="expense")
    await state.set_state(FinanceStates.amount)
    await message.answer("Введи сумму расхода в рублях:")


# ВВОД СУММЫ
@dp.message(FinanceStates.amount)
async def process_amount(message: Message, state: FSMContext):
    if not is_owner(message):
        return

    try:
        value = Decimal(message.text.strip().replace(" ", "").replace(",", "."))
        if not value.is_finite() or value <= 0 or value > Decimal("100000000"):
            raise ValueError
        cents = int(value * 100)
        if Decimal(cents) / 100 != value:
            raise ValueError
    except (InvalidOperation, ValueError, AttributeError):
        await message.answer("Введи корректную сумму, например: 1500 или 1500,50")
        return

    await state.update_data(amount=cents)
    await state.set_state(FinanceStates.category)
    await message.answer(
        "Укажи категорию операции:\n"
        "Например: зарплата, продукты, автомобиль, развлечения."
    )


# ВВОД КАТЕГОРИИ
@dp.message(FinanceStates.category)
async def process_category(message: Message, state: FSMContext):
    if not is_owner(message):
        return

    category = (message.text or "").strip()
    if not category or len(category) > 40:
        await message.answer("Категория должна содержать от 1 до 40 символов.")
        return

    await state.update_data(category=category)
    await state.set_state(FinanceStates.comment)
    await message.answer("Добавь комментарий или отправь точку (.) без комментария.")


# СОХРАНЕНИЕ ОПЕРАЦИИ
@dp.message(FinanceStates.comment)
async def process_comment(message: Message, state: FSMContext):
    if not is_owner(message):
        return

    comment = (message.text or "").strip()
    if len(comment) > 200:
        await message.answer("Комментарий слишком длинный. Максимум 200 символов.")
        return

    data = await state.get_data()
    if comment == ".":
        comment = ""

    with sqlite3.connect(DB_NAME) as db:
        db.execute("""
            INSERT INTO operations
            (owner_id, type, amount, category, comment, created_at)
            VALUES (?, ?, ?, ?, ?, ?)
        """, (
            OWNER_ID,
            data["operation_type"],
            data["amount"],
            data["category"],
            comment,
            datetime.now().strftime("%d.%m.%Y %H:%M")
        ))

    operation = "Доход" if data["operation_type"] == "income" else "Расход"

    await state.clear()
    await message.answer(
        f"✅ <b>{operation} сохранён!</b>\n"
        f"Сумма: {money(data['amount'])}\n"
        f"Категория: {data['category']}\n"
        f"Баланс: {money(get_balance())}",
        reply_markup=menu
    )


# БАЛАНС
@dp.message(F.text == "💰 Баланс")
async def balance(message: Message):
    if not is_owner(message):
        return

    await message.answer(
        f"💰 <b>Текущий баланс:</b>\n{money(get_balance())}"
    )


# ИСТОРИЯ
@dp.message(F.text == "📜 История")
async def history(message: Message):
    if not is_owner(message):
        return

    with sqlite3.connect(DB_NAME) as db:
        rows = db.execute("""
            SELECT type, amount, category, comment, created_at
            FROM operations
            WHERE owner_id = ?
            ORDER BY id DESC LIMIT 10
        """, (OWNER_ID,)).fetchall()

    if not rows:
        await message.answer("Операций пока нет.")
        return

    result = "📜 <b>Последние 10 операций:</b>\n\n"
    for kind, amount, category, comment, date in rows:
        sign = "+" if kind == "income" else "−"
        result += (
            f"{sign}{money(amount)} — {category}\n"
            f"<i>{date}</i>\n"
        )
        if comment:
            result += f"{comment}\n"
        result += "\n"

    await message.answer(result)


# СТАТИСТИКА
@dp.message(F.text == "📊 Статистика")
async def statistics(message: Message):
    if not is_owner(message):
        return

    month = datetime.now().strftime("%m.%Y")

    with sqlite3.connect(DB_NAME) as db:
        rows = db.execute("""
            SELECT type, COALESCE(SUM(amount), 0)
            FROM operations
            WHERE owner_id = ? AND substr(created_at, 4, 7) = ?
            GROUP BY type
        """, (OWNER_ID, month)).fetchall()

    totals = dict(rows)
    income = totals.get("income", 0)
    expense = totals.get("expense", 0)

    await message.answer(
        f"📊 <b>Статистика за {month}</b>\n\n"
        f"🟢 Доходы: {money(income)}\n"
        f"🔴 Расходы: {money(expense)}\n"
        f"💰 Разница: {money(income - expense)}"
    )


async def main():
    init_db()
    bot = Bot(
        token=BOT_TOKEN,
        default=DefaultBotProperties(parse_mode=ParseMode.HTML)
    )
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
