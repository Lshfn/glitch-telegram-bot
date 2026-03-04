import os
import random
import asyncio
from aiohttp import web
from aiogram import Bot, Dispatcher, types

BOT_TOKEN = os.getenv("BOT_TOKEN")
WEBHOOK_URL = os.getenv("WEBHOOK_URL")

if not BOT_TOKEN:
    raise ValueError("BOT_TOKEN not set")

bot = Bot(token=BOT_TOKEN)
dp = Dispatcher()

STATE_FILE = "state.txt"


# =========================
# СОСТОЯНИЕ СТАРЕНИЯ
# =========================

def get_age():
    if not os.path.exists(STATE_FILE):
        return 0
    with open(STATE_FILE, "r") as f:
        return int(f.read().strip())


def increase_age():
    age = get_age() + 1
    with open(STATE_FILE, "w") as f:
        f.write(str(age))
    return age


# =========================
# JPEG ГЛИТЧ (аккуратный)
# =========================

def glitch_jpeg(data, age):
    data = bytearray(data)

    # не трогаем первые 1000 байт (заголовки)
    start = 1000
    intensity = min(age / 50, 1)

    for _ in range(int(len(data) * 0.01 * intensity)):
        i = random.randint(start, len(data) - 1)
        data[i] = random.randint(0, 255)

    return bytes(data)


# =========================
# MP4 ГЛИТЧ
# =========================

def glitch_mp4(data, age):
    data = bytearray(data)
    intensity = min(age / 30, 1)

    for _ in range(int(len(data) * 0.005 * intensity)):
        i = random.randint(2000, len(data) - 1)
        data[i] ^= random.randint(1, 255)

    if age > 20:
        # иногда ломаем атомы структуры
        for _ in range(20):
            pos = random.randint(0, len(data)-4)
            data[pos:pos+4] = b"VOID"

    return bytes(data)


# =========================
# ОБЩИЙ ГЛИТЧ
# =========================

def generic_glitch(data, age):
    data = bytearray(data)
    intensity = min(age / 40, 1)

    for _ in range(int(len(data) * 0.02 * intensity)):
        i = random.randint(0, len(data) - 1)
        data[i] = random.randint(0, 255)

    return bytes(data)


# =========================
# ЗАГРУЗКА
# =========================

async def unstable_loading(message, age):
    msg = await message.answer("Uploading.")
    await asyncio.sleep(0.5)

    if age > 10:
        await msg.edit_text("Reconstructing memory blocks...")
        await asyncio.sleep(1)

    if age > 25 and random.random() < 0.2:
        await msg.edit_text("Uploading...")
        await asyncio.sleep(9999)

    return msg


# =========================
# ОБРАБОТКА
# =========================

@dp.message()
async def chaos(message: types.Message):

    file_id = None
    filename_hint = ""

    if message.document:
        file_id = message.document.file_id
        filename_hint = message.document.file_name or ""
    elif message.photo:
        file_id = message.photo[-1].file_id
        filename_hint = "photo.jpg"
    elif message.video:
        file_id = message.video.file_id
        filename_hint = "video.mp4"
    else:
        await message.answer("Send file / photo / video.")
        return

    age = increase_age()

    loading_msg = await unstable_loading(message, age)

    file = await bot.get_file(file_id)
    downloaded = await bot.download_file(file.file_path)
    original_bytes = downloaded.read()

    lower = filename_hint.lower()

    if lower.endswith(".jpg") or lower.endswith(".jpeg"):
        glitched = glitch_jpeg(original_bytes, age)

    elif lower.endswith(".mp4"):
        glitched = glitch_mp4(original_bytes, age)

    else:
        glitched = generic_glitch(original_bytes, age)

    filename = f"glitch_age{age}.bin"

    with open(filename, "wb") as f:
        f.write(glitched)

    await asyncio.sleep(random.uniform(0.2, 1.5))

    await message.answer_document(types.FSInputFile(filename))
    await loading_msg.delete()

    os.remove(filename)


# =========================
# WEBHOOK
# =========================

async def handle(request):
    data = await request.json()
    update = types.Update(**data)
    await dp.feed_update(bot, update)
    return web.Response()

async def on_startup(app):
    await bot.set_webhook(WEBHOOK_URL)

async def on_shutdown(app):
    await bot.delete_webhook()

def main():
    app = web.Application()
    app.router.add_post("/", handle)
    app.on_startup.append(on_startup)
    app.on_shutdown.append(on_shutdown)

    port = int(os.environ.get("PORT", 8000))
    web.run_app(app, host="0.0.0.0", port=port)

if __name__ == "__main__":
    main()
