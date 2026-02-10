import asyncio
import os
from telethon import TelegramClient, events
from dotenv import load_dotenv
from collections import defaultdict
from datetime import datetime
import requests
from requests.exceptions import ReadTimeout

# ================== LOAD ENV ==================
load_dotenv()

api_id = int(os.getenv("TG_API_ID"))
api_hash = os.getenv("TG_API_HASH")
ai_api_key = os.getenv("AI_API_KEY")

SESSION = os.getenv("TG_SESSION", "tg_listener")
TARGET_CHANNEL = os.getenv("TG_TARGET_CHANNEL")

# ==============================================

SOURCE_CHANNELS = [
    "klops_news", "ruwestru", "kpkld", "newchernyakhovsk", "kenig01",
    "gtrk_kaliningrad", "mygurievsk", "kaliningrad_chp", "kaliningrad_smi",
    "amberdlb", "baltiknews", "balt_kld", "kaliningradru", "amber_mash",
    "newkal_stream", "rugrad", "tgkld", "glavnoe39", "balt4post", "kaskad_tv",
    "kaliningrad_novosty", "chestnoklgd", "chtotamkaliningrad", "kaliningradrad",
    "kaliningrad_MIR", "Kaliningrad_life", "glavche", "kaliningrad_online",
    "Kalina39info"
]

COPY_MODE = False
THROTTLE_SECONDS = 10

MAX_RETRIES = 5
RETRY_DELAY = 10  # секунд

client = TelegramClient(SESSION, api_id, api_hash)

source_ids = set()
target_id = None

ai_queue = asyncio.Queue()
post_queue = asyncio.Queue()

albums = defaultdict(list)

# ================== ЛОГ ==================
def log(msg):
    print(f"[{datetime.now().strftime('%H:%M:%S')}] {msg}")

# ================== AI АНАЛИЗ ==================
async def analyze_post_with_ai(text: str) -> bool:
    if not text or not text.strip():
        return False

    if not ai_api_key:
        log("❌ AI_API_KEY не задан")
        return False

    prompt = f"""
Ты — система фильтрации контента.

Определи, соответствует ли текст происшествию,
связанному с военными или силовыми структурами РФ.

Верни ТОЛЬКО true или false.

Текст:
{text}
""".strip()

    for attempt in range(1, MAX_RETRIES + 1):
        try:
            log(f"AI запрос попытка {attempt}/{MAX_RETRIES}")

            response = requests.post(
                "https://apifreellm.com/api/v1/chat",
                headers={
                    "Content-Type": "application/json",
                    "Authorization": f"Bearer {ai_api_key}"
                },
                json={"message": prompt},
                timeout=60
            )

            if response.status_code != 200:
                log(f"❌ AI API HTTP ошибка: {response.status_code}")
                return False

            data = response.json()

            if not data.get("success"):
                log("❌ AI API success = false")
                return False

            result = str(data.get("response", "")).strip().lower()

            delay = data.get("features", {}).get("delaySeconds")
            if delay:
                await asyncio.sleep(delay)

            return result == "true"

        except ReadTimeout:
            log(f"⏳ Таймаут AI (попытка {attempt})")

            if attempt < MAX_RETRIES:
                log(f"Повтор через {RETRY_DELAY} сек...")
                await asyncio.sleep(RETRY_DELAY)
            else:
                log("❌ AI запрос окончательно упал по таймауту")
                return False

        except Exception as e:
            log(f"❌ AI ошибка: {repr(e)}")
            return False

# ================== ИНИЦИАЛИЗАЦИЯ ==================
async def warmup():
    dialogs = await client.get_dialogs(limit=500)
    log(f"Прогрев: загружено диалогов — {len(dialogs)}")

async def resolve_sources():
    for ch in SOURCE_CHANNELS:
        try:
            entity = await client.get_entity(ch)
            peer_id = entity.id if entity.id < 0 else int(f"-100{entity.id}")
            source_ids.add(peer_id)
            log(f"Источник: {entity.title}")
        except Exception as e:
            log(f"Источник пропущен: {ch} — {e}")

    log(f"Всего источников: {len(source_ids)}")

async def resolve_target():
    global target_id
    entity = await client.get_entity(TARGET_CHANNEL)
    target_id = entity.id
    log(f"Целевой канал: {entity.title}")

# ================== ОБРАБОТЧИК ==================
@client.on(events.NewMessage)
async def handler(event):
    if event.chat_id not in source_ids:
        return

    msg = event.message

    if not msg.post:
        return

    if msg.grouped_id:
        albums[msg.grouped_id].append(msg)
        await asyncio.sleep(1)

        if msg.grouped_id in albums:
            messages = albums.pop(msg.grouped_id)
            await ai_queue.put(messages)
            log(f"Альбом добавлен в AI очередь ({len(messages)} элементов)")
    else:
        await ai_queue.put([msg])
        log(f"Пост {msg.id} добавлен в AI очередь")

# ================== AI WORKER ==================
async def ai_worker():
    log("AI воркер запущен")

    while True:
        messages = await ai_queue.get()

        try:
            log("AI анализирует пост...")
            is_filtered = await analyze_post_with_ai(messages[0].text)

            if is_filtered:
                await post_queue.put(messages)
                log("Пост прошёл фильтр → в очередь отправки")
            else:
                log("Пост не прошёл фильтр")

        except Exception as e:
            log(f"Ошибка AI воркера: {repr(e)}")

        await asyncio.sleep(THROTTLE_SECONDS)
        ai_queue.task_done()

# ================== SENDER WORKER ==================
async def sender_worker():
    log("Воркер отправки запущен")

    while True:
        messages = await post_queue.get()

        try:
            if COPY_MODE:
                await client.send_message(
                    TARGET_CHANNEL,
                    messages[0].text,
                    file=[m.media for m in messages if m.media]
                )
            else:
                await client.forward_messages(
                    TARGET_CHANNEL,
                    messages
                )

            log("Пост отправлен")

        except Exception as e:
            log(f"Ошибка отправки: {repr(e)}")

        await asyncio.sleep(THROTTLE_SECONDS)
        post_queue.task_done()

# ================== MAIN ==================
async def main():
    await client.start()
    log("Telegram клиент запущен")

    await warmup()
    await resolve_target()
    await resolve_sources()

    asyncio.create_task(ai_worker())
    asyncio.create_task(sender_worker())

    log("Система запущена и ожидает посты")
    await client.run_until_disconnected()

if __name__ == "__main__":
    asyncio.run(main())
