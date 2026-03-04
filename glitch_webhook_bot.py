import os
import random
import asyncio
from io import BytesIO

from aiogram import Bot, Dispatcher, types
from aiogram.filters import Command
from aiogram.types import ContentType, InputFile
from PIL import Image
import numpy as np

TOKEN = os.environ.get("TELEGRAM_TOKEN")
bot = Bot(token=TOKEN)
dp = Dispatcher()

# ---------------- Glitch utilities ----------------
def glitch_bytes(data: bytes, level=0.1):
    arr = bytearray(data)
    n = max(1, int(len(arr) * level))
    for _ in range(n):
        i, j = random.randint(0, len(arr)-1), random.randint(0, len(arr)-1)
        arr[i], arr[j] = arr[j], arr[i]
    return bytes(arr)

def glitch_image(img: Image.Image, level=0.05):
    arr = np.array(img)
    h, w = arr.shape[:2]
    for _ in range(int(level * h * w)):
        x1, y1 = random.randint(0, w-1), random.randint(0, h-1)
        x2, y2 = random.randint(0, w-1), random.randint(0, h-1)
        arr[y1, x1], arr[y2, x2] = arr[y2, x2], arr[y1, x1]
    return Image.fromarray(arr)

def escalating_glitch(data: bytes, steps=3):
    return [glitch_bytes(data, level=(i+1)/steps/2) for i in range(steps)]

# ---------------- Bot handlers ----------------
@dp.message(Command("start"))
async def start_cmd(message: types.Message):
    await message.reply(
        "Hi! In Russia, messenger and social media blocks are nothing surprising. "
        "Let's turn Telegram slowdowns into art. Upload a file and get glitched versions!"
    )

@dp.message(content_types=ContentType.ANY)
async def handle_file(message: types.Message):
    file_bytes = BytesIO()
    filename = "glitch.bin"

    # --- Download user file ---
    if message.content_type == ContentType.PHOTO:
        photo = message.photo[-1]
        await photo.download(destination_file=file_bytes)
        filename = "glitch.jpg"
    elif message.content_type == ContentType.VIDEO:
        await message.video.download(destination_file=file_bytes)
        filename = "glitch.mp4"
    elif message.content_type == ContentType.AUDIO:
        await message.audio.download(destination_file=file_bytes)
        filename = "glitch.mp3"
    elif message.content_type == ContentType.DOCUMENT:
        await message.document.download(destination_file=file_bytes)
        ext = os.path.splitext(message.document.file_name)[-1] or ".txt"
        filename = f"glitch{ext}"
    elif message.content_type == ContentType.ANIMATION:  # GIF
        await message.animation.download(destination_file=file_bytes)
        filename = "glitch.gif"
    else:
        await message.reply("File type not supported.")
        return

    # --- Random infinite load ---
    if random.random() < 0.05:
        await message.reply("Upload seems stuck... 🔄")
        while True: await asyncio.sleep(999)

    file_bytes.seek(0)
    data = file_bytes.read()
    variants = escalating_glitch(data, steps=3)

    # --- Send glitched versions ---
    for i, v in enumerate(variants):
        bio = BytesIO()
        # --- Format-specific handling ---
        if message.content_type == ContentType.PHOTO:
            img = Image.open(BytesIO(v))
            img.save(bio, format="JPEG")
            bio.name = f"glitch{i+1}.jpg"
        elif message.content_type == ContentType.VIDEO:
            bio.write(v)
            bio.name = f"glitch{i+1}.mp4"
        elif message.content_type == ContentType.AUDIO:
            bio.write(v)
            bio.name = f"glitch{i+1}.mp3"
        else:  # documents, GIFs
            bio.write(v)
            ext = os.path.splitext(filename)[-1]
            bio.name = f"glitch{i+1}{ext}"

        bio.seek(0)
        await message.answer_document(InputFile(bio, filename=bio.name))
        await asyncio.sleep(0.5 + random.random())

# ---------------- Run bot ----------------
if __name__ == "__main__":
    import asyncio
    asyncio.run(dp.start_polling(bot))
