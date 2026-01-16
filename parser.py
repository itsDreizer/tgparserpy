import asyncio
import os
from telethon import TelegramClient, events
from dotenv import load_dotenv
from collections import defaultdict
from datetime import datetime

# ================== LOAD ENV ==================
load_dotenv()

api_id = int(os.getenv("TG_API_ID"))
api_hash = os.getenv("TG_API_HASH")
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

COPY_MODE = False          # False = forward, True = copy
THROTTLE_SECONDS = 2       # задержка между постами

client = TelegramClient(SESSION, api_id, api_hash)

source_ids = set()
target_id = None

post_queue = asyncio.Queue()
albums = defaultdict(list)


# ================== LOG ==================
def log(msg):
    print(f"[{datetime.now().strftime('%H:%M:%S')}] {msg}")


# ================== SETUP ==================
async def warmup():
    dialogs = await client.get_dialogs(limit=500)
    log(f"[warmup] dialogs loaded: {len(dialogs)}")


async def resolve_sources():
    for ch in SOURCE_CHANNELS:
        try:
            entity = await client.get_entity(ch)

            # Приводим username → peer_id (-100...)
            peer_id = entity.id if entity.id < 0 else int(f"-100{entity.id}")
            source_ids.add(peer_id)

            log(f"[source OK] {entity.title} → peer_id={peer_id}")

        except Exception as e:
            log(f"[source SKIP] {ch}: {e}")

    log(f"[DEBUG] source_ids = {source_ids}")


async def resolve_target():
    global target_id
    entity = await client.get_entity(TARGET_CHANNEL)
    target_id = entity.id
    log(f"[target OK] {entity.title} ({target_id})")


async def send_test_message():
    await client.send_message(
        TARGET_CHANNEL,
        "✅ Listener запущен и готов пересылать посты"
    )
    log("[test] Test message sent")


# ================== HANDLER ==================
@client.on(events.NewMessage)
async def handler(event):
    log(
        f"[event] chat_id={event.chat_id} "
        f"msg_id={event.message.id} "
        f"post={event.message.post} "
        f"grouped_id={event.message.grouped_id}"
    )

    if event.chat_id not in source_ids:
        log("[skip] not in source_ids")
        return

    msg = event.message

    if not msg.post:
        log("[skip] not a channel post")
        return

    # ===== ALBUM =====
    if msg.grouped_id:
        albums[msg.grouped_id].append(msg)
        log(
            f"[album] add msg {msg.id} "
            f"group={msg.grouped_id} "
            f"size={len(albums[msg.grouped_id])}"
        )

        await asyncio.sleep(1)

        if msg.grouped_id in albums:
            messages = albums.pop(msg.grouped_id)
            await post_queue.put(messages)
            log(
                f"[album] finalized group {msg.grouped_id}, "
                f"{len(messages)} msgs → queue"
            )
    else:
        await post_queue.put([msg])
        log(f"[queue] single msg {msg.id} added")


# ================== SENDER ==================
async def sender_worker():
    log("[worker] sender worker started")

    while True:
        messages = await post_queue.get()
        log(f"[worker] got post from queue ({len(messages)} msgs)")

        try:
            if COPY_MODE:
                log("[send] COPY_MODE")
                await client.send_message(
                    TARGET_CHANNEL,
                    messages[0].text,
                    file=[m.media for m in messages if m.media]
                )
            else:
                log("[send] FORWARD_MODE")
                await client.forward_messages(
                    TARGET_CHANNEL,
                    messages
                )

            log("[send OK] post sent")

        except Exception as e:
            log(f"[SEND ERROR] {repr(e)}")

        await asyncio.sleep(THROTTLE_SECONDS)
        post_queue.task_done()


# ================== MAIN ==================
async def main():
    await client.start()
    log("[+] Client started")

    await warmup()
    await resolve_target()
    await resolve_sources()
    await send_test_message()

    asyncio.create_task(sender_worker())

    log("[+] Listener is running")
    await client.run_until_disconnected()


if __name__ == "__main__":
    asyncio.run(main())
