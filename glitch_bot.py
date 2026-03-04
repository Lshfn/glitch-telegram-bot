import os
import asyncio
import random
from aiogram import Bot, Dispatcher, types
from aiogram.types import FSInputFile

BOT_TOKEN = os.getenv("BOT_TOKEN")

if not BOT_TOKEN:
    raise ValueError("BOT_TOKEN not set")

bot = Bot(token=BOT_TOKEN)
dp = Dispatcher()


# =========================
# ЗАГРУЗОЧНЫЕ ИЛЛЮЗИИ
# =========================

async def fake_progress(message):
    msg = await message.answer("Uploading: 0%")
    for i in range(1, 6):
        await asyncio.sleep(random.uniform(0.3, 0.9))
        await msg.edit_text(f"Uploading: {i*random.randint(10,25)}%")
    return msg


async def infinite_loading(message):
    msg = await message.answer("Uploading...")
    await asyncio.sleep(random.uniform(5, 10))
    await msg.edit_text("Uploading........")
    await asyncio.sleep(9999)


async def dissolving_loading(message):
    msg = await message.answer("█▒▒▒▒▒▒▒▒▒")
    bars = [
        "██▒▒▒▒▒▒▒▒",
        "███▒▒▒▒▒▒▒",
        "████▒▒▒▒▒▒",
        "███▒▒▒▒▒▒▒",
        "██▒▒▒▒▒▒▒▒",
        "█▒▒▒▒▒▒▒▒▒",
        "▒▒▒▒▒▒▒▒▒▒"
    ]
    for b in bars:
        await asyncio.sleep(0.5)
        await msg.edit_text(b)
    return msg


# =========================
# ГЛИТЧ ФУНКЦИИ
# =========================

def byte_rot(data):
    for _ in range(int(len(data) * 0.02)):
        i = random.randint(0, len(data)-1)
        data[i] = random.randint(0, 255)
    return data


def striped_decay(data):
    step = random.randint(50, 300)
    for i in range(0, len(data), step):
        data[i:i+10] = b'\x00' * min(10, len(data)-i)
    return data


def mid_burn(data):
    center = len(data)//2
    span = len(data)//6
    data[center-span:center+span] = os.urandom(span*2)
    return data


def slow_corruption(data):
    for i in range(0, len(data), 100):
        if random.random() > 0.7:
            data[i] = random.randint(0, 255)
    return data


def glitch_file(original_bytes):
    data = bytearray(original_bytes)

    mode = random.choice([
        byte_rot,
        striped_decay,
        mid_burn,
        slow_corruption
    ])

    return mode(data)


# =========================
# ОБРАБОТКА СООБЩЕНИЙ
# =========================

@dp.message()
async def chaos_handler(message: types.Message):

    # выбираем режим загрузки
    loading_mode = random.choice([
        fake_progress,
        dissolving_loading,
        None,
        infinite_loading
    ])

    if loading_mode == infinite_loading:
        await infinite_loading(message)
        return

    loading_msg = None
    if loading_mode:
        loading_msg = await loading_mode(message)

    file_id = None

    if message.document:
        file_id = message.document.file_id

    elif message.photo:
        file_id = message.photo[-1].file_id

    elif message.video:
        file_id = message.video.file_id

    else:
        await message.answer("Send file / photo / video.")
        return

    file = await bot.get_file(file_id)
    downloaded = await bot.download_file(file.file_path)
    original_bytes = downloaded.read()

    glitched_bytes = glitch_file(original_bytes)

    filename = f"glitched_{random.randint(1000,9999)}.bin"

    with open(filename, "wb") as f:
        f.write(glitched_bytes)

    await asyncio.sleep(random.uniform(0.5, 2))

    await message.answer_document(FSInputFile(filename))

    if loading_msg:
        await loading_msg.delete()

    os.remove(filename)


# =========================

async def main():
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
