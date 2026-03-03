import asyncio
import random
import logging
import os
from aiogram import Bot, Dispatcher, F
from aiogram.types import Message, FSInputFile
from aiogram.client.default import DefaultBotProperties
from aiogram import Router
import os

BOT_TOKEN = os.getenv("8332844538:AAHMhU-XEn1Umk0fpfBefc3bvST0o5su9BU")

if not BOT_TOKEN:
    raise ValueError("8332844538:AAHMhU-XEn1Umk0fpfBefc3bvST0o5su9BU")

logging.basicConfig(level=logging.INFO)

bot = Bot(
    token=BOT_TOKEN,
    default=DefaultBotProperties(parse_mode="HTML")
)

dp = Dispatcher()
router = Router()
dp.include_router(router)


# -----------------------------
# GLITCH MODES
# -----------------------------

def light_glitch(data):
    for _ in range(1000):
        if len(data) > 100:
            index = random.randint(50, len(data) - 50)
            data[index] = random.randint(0, 255)
    return data


def heavy_glitch(data):
    for _ in range(8000):
        if len(data) > 100:
            index = random.randint(50, len(data) - 50)
            data[index] = random.randint(0, 255)
    return data


def decay_glitch(data):
    # удаляем случайные куски (packet loss)
    for _ in range(50):
        if len(data) > 200:
            start = random.randint(100, len(data) - 100)
            length = random.randint(10, 200)
            data[start:start+length] = b'\x00' * length
    return data


def glitch_file(input_path, output_path):
    with open(input_path, "rb") as f:
        data = bytearray(f.read())

    mode = random.choice(["light", "heavy", "decay"])

    if mode == "light":
        data = light_glitch(data)
    elif mode == "heavy":
        data = heavy_glitch(data)
    else:
        data = decay_glitch(data)

    with open(output_path, "wb") as f:
        f.write(data)

    return mode


# -----------------------------
# START
# -----------------------------

@router.message(F.text == "/start")
async def start_handler(message: Message):
    await message.answer(
        "Archive node unstable.\nSend file.\nUpload may not complete."
    )


# -----------------------------
# FILE HANDLER
# -----------------------------

@router.message(F.document)
async def handle_file(message: Message):

    # вероятность бесконечной загрузки
    infinite_mode = random.random() < 0.25  # 25%

    progress_message = await message.answer("Uploading file... 0%")

    percent = 0

    # --- Infinite upload mode ---
    if infinite_mode:
        while True:
            await asyncio.sleep(random.uniform(1, 3))
            percent += random.randint(-3, 8)

            if percent < 0:
                percent = 0
            if percent > 97:
                percent = 96

            await progress_message.edit_text(
                f"Uploading file... {percent}%\nConnection unstable..."
            )

    # --- Normal unstable mode ---
    else:
        while percent < 100:
            await asyncio.sleep(random.uniform(0.5, 2.0))
            percent += random.randint(-2, 15)

            if percent < 0:
                percent = 0
            if percent > 100:
                percent = 100

            await progress_message.edit_text(
                f"Uploading file... {percent}%"
            )

            if random.random() < 0.1:
                await progress_message.edit_text(
                    f"Uploading file... {percent}%\nPacket loss detected..."
                )

        await progress_message.edit_text("Reconstructing fragment...")
        await asyncio.sleep(2)

        # скачать файл
        file = await bot.get_file(message.document.file_id)
        input_path = "input_file"
        output_path = "glitched_file"

        await bot.download_file(file.file_path, input_path)

        # глитч
        mode = glitch_file(input_path, output_path)

        await asyncio.sleep(2)

        caption = f"Recovered fragment.\nMode: {mode}"

        await message.answer_document(
            FSInputFile(output_path),
            caption=caption
        )

        await progress_message.delete()

        if os.path.exists(input_path):
            os.remove(input_path)
        if os.path.exists(output_path):
            os.remove(output_path)


# -----------------------------
# RUN
# -----------------------------

async def main():
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
