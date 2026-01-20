import asyncio
import os
from telethon import TelegramClient, events
from dotenv import load_dotenv
from collections import defaultdict
from datetime import datetime

import cohere


# ================== LOAD ENV ==================
load_dotenv()

api_id = int(os.getenv("TG_API_ID"))
api_hash = os.getenv("TG_API_HASH")
ai_api_key = os.getenv("AI_API_KEY")
ai = cohere.ClientV2(api_key=ai_api_key)
SESSION = os.getenv("TG_SESSION", "tg_listener")
TARGET_CHANNEL = os.getenv("TG_TARGET_CHANNEL")
# ==============================================

SOURCE_CHANNELS = [
    "klops_news",
    "ruwestru",
    "kpkld",
    "newchernyakhovsk",
    "kenig01",
    "gtrk_kaliningrad",
    "mygurievsk",
    "kaliningrad_chp",
    "kaliningrad_smi",
    "amberdlb",
    "baltiknews",
    "balt_kld",
    "kaliningradru",
    "amber_mash",
    "newkal_stream",
    "rugrad",
    "tgkld",
    "glavnoe39",
    "balt4post",
    "kaskad_tv",
    "kaliningrad_novosty",
    "chestnoklgd",
    "chtotamkaliningrad",
    "kaliningradrad",
    "kaliningrad_MIR",
    "Kaliningrad_life",
    "glavche",
    "kaliningrad_online",
]

COPY_MODE = False          # False = пересылка, True = копирование
THROTTLE_SECONDS = 2       # задержка между постами (сек)

client = TelegramClient(SESSION, api_id, api_hash)

source_ids = set()
target_id = None

post_queue = asyncio.Queue()
albums = defaultdict(list)


# ================== ЛОГ ==================
def log(msg):
    print(f"[{datetime.now().strftime('%H:%M:%S')}] {msg}")


# ================== AI АНАЛИЗ ==================
def analyze_post_with_ai(text: str) -> bool:
    if not text or not text.strip():
        return False

    prompt = f"""
Ты — система фильтрации контента.

Твоя задача:
Определить, соответствует ли текст поста указанному правилу.

Правило фильтрации:
Пост соответствует правилу, если в нём описывается происшествие
(инцидент, чрезвычайная ситуация, конфликт, преступление, авария
или иное нештатное событие), которое:

— либо непосредственно связано с действиями, бездействием или
участием военных или государственных силовых структур Российской
Федерации (СК, Росгвардия, МВД, Министерство обороны РФ,
Вооружённые силы РФ и др.) либо их сотрудников;

— либо произошло на территории, объектах или в учреждениях,
относящихся к военным или государственным силовым структурам РФ,
включая воинские части, военные базы, казармы, полигоны,
объекты Минобороны РФ, ведомственные здания и охраняемые объекты.

К происшествиям относятся, в том числе:
нападения, задержания, стрельба, взрывы, аварии, гибель или ранения
людей, нарушения техники безопасности, чрезвычайные ситуации,
уголовные инциденты, конфликты и иные нештатные события.

НЕ считается соответствием правилу, если государственные или
силовые структуры лишь:
- проводят проверку
- принимают материалы
- дают комментарии
- расследуют бытовые, трудовые или гражданские происшествия,
  не связанные с их деятельностью или территорией.

Текст поста:
{text}

Инструкция:
- Верни ТОЛЬКО одно значение: true или false
- true — если текст соответствует правилу
- false — если не соответствует
- Не объясняй решение
- Не добавляй никакого текста кроме true или false
""".strip()

    try:
        response = ai.chat(
            model="command-a-03-2025",
            messages=[{"role": "user", "content": prompt}],
        )

        content = response.message.content

        if not content or not hasattr(content[0], "text"):
            return False

        result = content[0].text.strip().lower()
        return result == "true"

    except Exception as e:
        log(f"❌ Ошибка запроса к нейросети: {repr(e)}")
        return False


# ================== ИНИЦИАЛИЗАЦИЯ ==================
async def warmup():
    dialogs = await client.get_dialogs(limit=500)
    log(f"Прогрев клиента: загружено диалогов — {len(dialogs)}")


async def resolve_sources():
    for ch in SOURCE_CHANNELS:
        try:
            entity = await client.get_entity(ch)
            peer_id = entity.id if entity.id < 0 else int(f"-100{entity.id}")
            source_ids.add(peer_id)
            log(f"Источник добавлен: {entity.title} (peer_id={peer_id})")
        except Exception as e:
            log(f"Источник пропущен: {ch} — {e}")

    log(f"Всего источников подключено: {len(source_ids)}")


async def resolve_target():
    global target_id
    entity = await client.get_entity(TARGET_CHANNEL)
    target_id = entity.id
    log(f"Целевой канал: {entity.title} ({target_id})")


# ================== ОБРАБОТЧИК ==================
@client.on(events.NewMessage)
async def handler(event):
    if event.chat_id not in source_ids:
        return

    msg = event.message

    if not msg.post:
        log("Сообщение пропущено: не является постом канала")
        return

    # ===== АЛЬБОМ =====
    if msg.grouped_id:
        albums[msg.grouped_id].append(msg)
        log(
            f"Альбом: добавлено сообщение {msg.id} "
            f"(группа {msg.grouped_id}, всего {len(albums[msg.grouped_id])})"
        )

        await asyncio.sleep(1)

        if msg.grouped_id in albums:
            messages = albums.pop(msg.grouped_id)

            isFiltered = analyze_post_with_ai(messages[0].text)
            
            if not isFiltered:
                return
            
            await post_queue.put(messages)
            log(
                f"Альбом собран: {len(messages)} элементов "
                f"→ добавлено в очередь"
            )

    else:
        isFiltered = analyze_post_with_ai(msg.text)
        if not isFiltered:
            return
        
        await post_queue.put([msg])
        log(f"Одиночный пост {msg.id} добавлен в очередь")


# ================== ОТПРАВЩИК ==================
async def sender_worker():
    log("Воркер отправки запущен")

    while True:
        messages = await post_queue.get()
        log(f"Из очереди получен пост ({len(messages)} элементов)")

        try:
            if COPY_MODE:
                log("Режим: копирование сообщения")
                await client.send_message(
                    TARGET_CHANNEL,
                    messages[0].text,
                    file=[m.media for m in messages if m.media]
                )
            else:
                log("Режим: пересылка сообщения")
                await client.forward_messages(
                    TARGET_CHANNEL,
                    messages
                )

            log("Пост успешно отправлен")

        except Exception as e:
            log(f"Ошибка при отправке поста: {repr(e)}")

        await asyncio.sleep(THROTTLE_SECONDS)
        post_queue.task_done()


# ================== MAIN ==================
async def main():
    await client.start()
    log("Клиент Telegram запущен")

    await warmup()
    await resolve_target()
    await resolve_sources()

    asyncio.create_task(sender_worker())

    log("Listener запущен и ожидает новые посты")
    await client.run_until_disconnected()


if __name__ == "__main__":
    asyncio.run(main())
