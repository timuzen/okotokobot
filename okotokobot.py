from telegram import Update
from telegram.constants import ChatAction
from telegram.ext import Application, CommandHandler, MessageHandler, filters
from apscheduler.schedulers.background import BackgroundScheduler
from zoneinfo import ZoneInfo
from datetime import datetime, timedelta, time as dtime
import requests
import asyncio
import random
import json
import os
import atexit

# - - - - - - - - - - - - - OKOTOKOBOT - - - - - - - - - - - - - - -

STATE_FILE = "bot_state.json"
TOKEN = "7607565198:AAE4PEgwAdDBo2q-gF-FcWV6lXq9pgfehyU"q

scheduler = BackgroundScheduler(timezone=ZoneInfo("Asia/Almaty"))
scheduler.start()

active_chats = set()
just_started_chats = set()
recent_responded = set()  # анти дабл-мобл баг
last_message_ids = {}
last_emoji_message_ids = {}
user_hint_requests = {}
next_random_push = {}

EMOJIS = ["👁"]


# Сохранение состояния бота
def save_state():
    state = {
        "active_chats": list(active_chats),
        "next_random_push": {str(k): v.isoformat() for k, v in next_random_push.items()},
        "user_hint_requests": {str(k): {
            "time": v["time"].isoformat(),
            "count": v["count"]
        } for k, v in user_hint_requests.items()}
    }
    with open(STATE_FILE, "w") as f:
        json.dump(state, f)


# Возобновление состояния бота
def load_state():
    if not os.path.exists(STATE_FILE):
        return

    with open(STATE_FILE, "r") as f:
        state = json.load(f)

    active_chats.update(int(cid) for cid in state.get("active_chats", []))

    for cid, time_str in state.get("next_random_push", {}).items():
        next_random_push[int(cid)] = datetime.fromisoformat(time_str)

    for cid, data in state.get("user_hint_requests", {}).items():
        user_hint_requests[int(cid)] = {
            "time": datetime.fromisoformat(data["time"]),
            "count": data["count"]
        }


# Watchdog
def ping_betterstack():
    try:
        requests.get("https://uptime.betterstack.com/api/v1/heartbeat/zRLyYatoR4AXkijVzvkT55Nd")
        print("✅ Watchdog ping sent to BetterStack.")
    except Exception as e:
        print(f"❌ Watchdog error: {e}")


# Получение цитаты
def get_quote():
    url = "http://api.forismatic.com/api/1.0/"
    params = {"method": "getQuote", "format": "json", "lang": "ru"}
    try:
        response = requests.post(url, data=params, timeout=5)
        if response.status_code == 200:
            return response.json().get("quoteText", "Цитата не найдена.")
        return "Ошибка при получении цитаты."
    except:
        return "Ошибка соединения с API."


# Генерация случайного "времени момента" (один раз в неделю)
def generate_next_random_time(from_date=None):
    if not from_date:
        from_date = datetime.now()

    days_ahead = random.randint(7, 13)  # минимум через неделю
    random_day = from_date + timedelta(days=days_ahead)

    hour = random.randint(9, 22)
    minute = random.randint(0, 59)

    return datetime.combine(random_day.date(), dtime(hour, minute))


# Планировщик - проверяем раз в минуту "время момента"
async def check_random_quotes(app):
    now = datetime.now()
    for chat_id in list(active_chats):
        scheduled = next_random_push.get(chat_id)

        # если нет — создаём
        if not scheduled:
            next_random_push[chat_id] = generate_next_random_time(from_date=now)
            continue

        # если пора — отправляем
        if now >= scheduled:
            quote = get_quote()

            try:
                await app.bot.send_chat_action(chat_id=chat_id, action=ChatAction.TYPING)
                delay = min(len(quote) * 0.05, 5)
                await asyncio.sleep(delay)

                await app.bot.send_message(chat_id=chat_id, text=quote)
            except Exception as e:
                print(f"Ошибка при отправке спонтанной цитаты: {e}")

            # запланировать следующий раз
            next_random_push[chat_id] = generate_next_random_time()


# Обработка любых сообщений
async def eye_response(update: Update, context):
    chat_id = update.effective_chat.id
    text = update.message.text.lower()

    if text.strip() == "/start":
        return

    if chat_id in recent_responded:
        return

    recent_responded.add(chat_id)

    async def clear_flag():
        await asyncio.sleep(2)
        recent_responded.discard(chat_id)

    asyncio.create_task(clear_flag())

    if chat_id not in active_chats:
        active_chats.add(chat_id)
        await context.bot.send_chat_action(chat_id=chat_id, action=ChatAction.TYPING)
        await asyncio.sleep(2)
        await update.message.reply_text("Я слежу за тобой. Будь на связи — в нужный момент дам знак.")
        return

    keywords = [word.lower() for word in ["совет", "знак", "подсказка", "подсказку", "помоги", "помощь"]]
    if any(word in text for word in keywords):
        now = datetime.now()
        hint_data = user_hint_requests.get(chat_id)

        # Первый раз или час прошёл
        if not hint_data or now - hint_data["time"] > timedelta(hours=1):
            user_hint_requests[chat_id] = {"time": now, "count": 1}
            quote = get_quote()

            await context.bot.send_chat_action(chat_id=chat_id, action=ChatAction.TYPING)
            delay = min(len(quote) * 0.05, 5)
            await asyncio.sleep(delay)

            msg = await update.message.reply_text(quote)
            last_message_ids[chat_id] = msg.message_id
            return

        elif hint_data["count"] == 1:
            user_hint_requests[chat_id]["count"] += 1

            await context.bot.send_chat_action(chat_id=chat_id, action=ChatAction.TYPING)
            await asyncio.sleep(2)

            msg = await update.message.reply_text("Чем гуще словестный лес, тем проще заблудиться.")
            last_message_ids[chat_id] = msg.message_id
            return

        else:
            # count >= 2 — только эмодзи
            pass

    # 👁 стандартный эмодзи
    if chat_id in last_emoji_message_ids:
        try:
            await context.bot.delete_message(chat_id=chat_id, message_id=last_emoji_message_ids[chat_id])
        except Exception as e:
            print(f"Не удалось удалить эмодзи: {e}")

    emoji = random.choice(EMOJIS)
    msg = await update.message.reply_text(emoji)
    last_emoji_message_ids[chat_id] = msg.message_id


# /start
async def start(update: Update, context):
    chat_id = update.effective_chat.id

    just_started_chats.add(chat_id)

    async def clear_just_started():
        await asyncio.sleep(3)
        just_started_chats.discard(chat_id)

    asyncio.create_task(clear_just_started())

    if chat_id in active_chats:
        await update.message.reply_text("Всё под контролем — расслабься.")
    else:
        active_chats.add(chat_id)
        await context.bot.send_chat_action(chat_id=chat_id, action=ChatAction.TYPING)
        await asyncio.sleep(2)
        await update.message.reply_text("Я слежу за тобой. Будь на связи — в нужный момент дам знак.")


# /help
async def help_command(update: Update, context):
    await update.message.reply_text("Даже не надейся.")


# /stop
async def stop(update: Update, context):
    chat_id = update.effective_chat.id
    if chat_id in active_chats:
        active_chats.remove(chat_id)
        await update.message.reply_text("Пакеда! Если нужно будет присмотреть — всегда здесь и сейчас.")
    else:
        await update.message.reply_text("Больше не слежу за тобой — двигайся на ощупь.")


# Запуск приложения
app = Application.builder().token(TOKEN).build()

loop = asyncio.get_event_loop()

load_state()
print("Состояние загружено.")

atexit.register(save_state)
print("Автосохранение при выходе настроено.")

# Schedulers - - - - - - - - - - -
scheduler.add_job(lambda: asyncio.run_coroutine_threadsafe(check_random_quotes(app), loop), "interval", minutes=1)
scheduler.add_job(save_state, "interval", minutes=10)
scheduler.add_job(ping_betterstack, "interval", minutes=10)

# Heandlers - - - - - - - - - - - -
app.add_handler(CommandHandler("start", start))
app.add_handler(CommandHandler("help", help_command))
app.add_handler(CommandHandler("stop", stop))
app.add_handler(MessageHandler(filters.TEXT & (~filters.COMMAND), eye_response, block=False))

print("Бот запущен...")
app.run_polling()
