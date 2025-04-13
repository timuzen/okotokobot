from telegram import Update
from telegram.constants import ChatAction
from telegram.ext import Application, CommandHandler, MessageHandler, filters
from apscheduler.schedulers.background import BackgroundScheduler
from zoneinfo import ZoneInfo
from datetime import datetime, timedelta, time as dtime
from dotenv import load_dotenv
from flask import Flask, request
from threading import Thread
from config import config
from db_ping import check_db
import requests
import asyncpg
import asyncio
import random
import json
import os
import atexit


load_dotenv()

STATE_FILE = "bot_state.json"

flask_app = Flask(__name__)
scheduler = BackgroundScheduler(timezone=ZoneInfo("Asia/Almaty"))
scheduler.start()

active_chats = set()
just_started_chats = set()
recent_responded = set()  # анти дабл-мобл баг
last_message_ids = {}
last_emoji_message_ids = {}
user_hint_requests = {}
next_random_push = {}

TEMP_CONFIG_FILE = f"temp_config_{config['DB']['schema']}.json"
temp_config = {}


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
    print(f"💾 Сохранено {len(active_chats)} активных чатов")


# Кэширование из БД
async def refresh_temp_config():
    print("🔄 Обновление temp_config из базы...")
    db = config["DB"]
    conn = await asyncpg.connect(
        host=db["host"],
        port=db["port"],
        user=db["user"],
        password=db["password"],
        database=db["name"]
    )
    await conn.execute(f"SET search_path TO {db['schema']}")

    # Транформация json в текст, уберая ковычки
    rows = await conn.fetch("SELECT tag, message::text AS message FROM messages")
    await conn.close()

    data = {}
    for row in rows:
        tag = row["tag"]
        raw_value = row["message"]

        try:
            # Пробуем распарсить, чтобы избавиться от кавычек вокруг строки
            value = json.loads(raw_value)
        except Exception:
            value = raw_value  # если не получилось — оставим как есть

        data[tag] = value

    print(f"✅ Загружено {len(data)} тегов из базы")

    # сохранение в файл
    with open(TEMP_CONFIG_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

    # обновление глобального кэша
    global temp_config
    temp_config = data
    print("✅ temp_config обновлён и сохранён")


# Загрузка кэша
def load_temp_config():
    global temp_config
    if os.path.exists(TEMP_CONFIG_FILE):
        try:
            with open(TEMP_CONFIG_FILE, "r", encoding="utf-8") as f:
                temp_config = json.load(f)
            print("📦 temp_config загружен из файла")
        except Exception as e:
            print(f"❌ Ошибка при загрузке temp_config: {e}")
            temp_config = {}
    else:
        print("⚠️ temp_config.json не найден — будет создан при следующем обновлении")


# Запуск сервера для мониторинга
@flask_app.route("/ping")
def ping():
    real_ip = request.headers.get("X-Forwarded-For", request.remote_addr)
    user_agent = request.headers.get("User-Agent", "Unknown")

    print(f"[PING] IP: {real_ip} | UA: {user_agent}")
    return "OK", 200

def run_flask():
    port = int(os.environ.get("PORT", 10000))
    flask_app.run(host="0.0.0.0", port=port)

# БД пинг
@flask_app.route("/db-ping")
def db_ping_endpoint():
    try:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        result = loop.run_until_complete(check_db())
        if result:
            return "DB OK", 200
        else:
            return "DB ERROR", 500
    except Exception as e:
        print(f"❌ Ошибка в /db-ping: {e}")
        return "Internal Error", 500


# Получение цитаты из внешнего сервера
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


# Вызов сообщений из кэша
async def get_message(tag: str):
    return temp_config.get(tag)

# Парсинг
async def get_json(tag: str):
    raw = await get_message(tag)
    if raw is None:
        return []

    if isinstance(raw, str):
        try:
            return json.loads(raw)
        except Exception as e:
            print(f"❌ Ошибка при парсинге JSON {tag}: {e}")
            return []

    return raw  # если это уже list или dict



# Планировщик - проверяем раз в минуту "время случайного момента"
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
        msg = await get_message("first_start")
        if isinstance(msg, str):
            await update.message.reply_text(msg)
        return

    keywords = await get_json("keywords")
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

            response = await get_message("second_response")
            if isinstance(response, str):
                msg = await update.message.reply_text(response)
                last_message_ids[chat_id] = msg.message_id
            return

        else:
            # count >= 2 — только эмодзи
            emojis = await get_json("emojis")
            if emojis:
                emoji = random.choice(emojis)

                # удалим старый эмодзи
                if chat_id in last_emoji_message_ids:
                    try:
                        await context.bot.delete_message(chat_id=chat_id, message_id=last_emoji_message_ids[chat_id])
                    except Exception as e:
                        print(f"Не удалось удалить эмодзи: {e}")

                try:
                    await context.bot.send_chat_action(chat_id=chat_id, action=ChatAction.TYPING)
                    await asyncio.sleep(1)

                    msg = await update.message.reply_text(emoji)
                    last_emoji_message_ids[chat_id] = msg.message_id
                except Exception as e:
                    print(f"❌ Не удалось отправить эмодзи: {e}")
            return

    # если ни ключевое слово, ни /start — просто покажем эмодзи
    # удалим старый, если был
    if chat_id in last_emoji_message_ids:
        try:
            await context.bot.delete_message(chat_id=chat_id, message_id=last_emoji_message_ids[chat_id])
        except Exception as e:
            print(f"Не удалось удалить эмодзи: {e}")

    emojis = await get_json("emojis")
    if emojis:
        emoji = random.choice(emojis)
        try:
            await context.bot.send_chat_action(chat_id=chat_id, action=ChatAction.TYPING)
            await asyncio.sleep(1)

            msg = await update.message.reply_text(emoji)
            last_emoji_message_ids[chat_id] = msg.message_id
        except Exception as e:
            print(f"❌ Не удалось отправить эмодзи: {e}")



# /start
async def start(update: Update, context):
    chat_id = update.effective_chat.id
    just_started_chats.add(chat_id)
    async def clear_just_started():
        await asyncio.sleep(3)
        just_started_chats.discard(chat_id)
    asyncio.create_task(clear_just_started())

    if chat_id in active_chats:
        msg = await get_message("repeated_start")
    else:
        active_chats.add(chat_id)
        await context.bot.send_chat_action(chat_id=chat_id, action=ChatAction.TYPING)
        await asyncio.sleep(2)
        msg = await get_message("first_start")

    if isinstance(msg, str):
            await update.message.reply_text(msg)

# /help
async def help_command(update: Update, context):
    msg = await get_message("help_response")
    if isinstance(msg, str):
        await update.message.reply_text(msg)


# /stop
async def stop(update: Update, context):
    chat_id = update.effective_chat.id
    if chat_id in active_chats:
        active_chats.remove(chat_id)
        msg = await get_message("stop_response")
    else:
        msg = await get_message("repeated_stop")

    if isinstance(msg, str):
        await update.message.reply_text(msg)


# Расписание
def setup_schedulers(app, loop):
    scheduler.add_job(lambda: asyncio.run_coroutine_threadsafe(check_random_quotes(app), loop), "interval", minutes=1)
    scheduler.add_job(save_state, "interval", minutes=10)
    scheduler.add_job(lambda: asyncio.run_coroutine_threadsafe(refresh_temp_config(), loop), "interval", minutes=60)

        

# Запуск приложения
app = Application.builder().token(config["TOKEN"]).build()

loop = asyncio.new_event_loop()
asyncio.set_event_loop(loop)

load_state()
print("Состояние загружено.")

atexit.register(save_state)
print("Автосохранение при выходе настроено.")

load_temp_config()
asyncio.run(refresh_temp_config())



# Schedulers - - - - - - - - - - -
setup_schedulers(app, loop)


# Heandlers - - - - - - - - - - - -
app.add_handler(CommandHandler("start", start))
app.add_handler(CommandHandler("help", help_command))
app.add_handler(CommandHandler("stop", stop))
app.add_handler(MessageHandler(filters.TEXT & (~filters.COMMAND), eye_response, block=False))

if __name__ == "__main__":
    # 1. Запуск Flask в фоновом потоке
    flask_thread = Thread(target=run_flask, daemon=True)
    flask_thread.start()

    # 2. Установка event loop (для Py 3.10+)
    import sys
    if sys.version_info >= (3, 10):
        asyncio.set_event_loop_policy(asyncio.DefaultEventLoopPolicy())

    print("Бот запущен...")
    app.run_polling()



