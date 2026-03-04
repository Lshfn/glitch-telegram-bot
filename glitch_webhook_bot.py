import asyncio
import random
from io import BytesIO
import os

from aiogram import Bot, Dispatcher, types
from aiogram.types import ContentType
from aiogram.filters import Command
from PIL import Image, ImageChops
import moviepy.editor as mp
import numpy as np

TOKEN = os.environ.get("TELEGRAM_TOKEN")
WEBHOOK_URL = os.environ.get("WEBHOOK_URL")

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
    h, w, c = arr.shape
    for _ in range(int(level * h * w)):
        x1, y1 = random.randint(0,w-1), random.randint(0,h-1)
        x2, y2 = random.randint(0,w-1), random.randint(0,h-1)
        arr[y1,x1], arr[y2,x2] = arr[y2,x2], arr[y1,x1]
    return Image.fromarray(arr)

def glitch_video_bytes(video_bytes: bytes, level=0.05):
    bio = BytesIO(video_bytes)
    try:
        clip = mp.VideoFileClip(bio)
        # randomly drop frames or swap pixels
        arr = np.array([frame for frame in clip.iter_frames()])
        idx = np.random.randint(0, len(arr), int(len(arr)*level))
        arr[idx] = arr[idx][::-1]  # reverse frames for glitch
        return arr.tobytes()
    except Exception:
        return video_bytes

def escalating_glitch(data: bytes, steps=5, fmt="bin"):
    results = []
    for i in range(steps):
        level = (i+1)/steps/2
        results.append(glitch_bytes(data, level))
    return results

# ---------------- Handlers ----------------
@dp.message(Command("start"))
async def start_cmd(message: types.Message):
    await message.reply(
        "Hi! In Russia, messenger and social media blocks are nothing surprising. "
        "Let's turn Telegram slowdowns into art. Upload a file and get glitched versions!"
    )

@dp.message(content_types=ContentType.ANY)
async def handle_file(message: types.Message):
    file_bytes = None
    filename = "glitch.bin"

    if message.content_type == ContentType.PHOTO:
        photo = message.photo[-1]
        file_bytes = await photo.download(destination=BytesIO())
        filename = "glitch.jpg"
    elif message.content_type == ContentType.VIDEO:
        file_bytes = await message.video.download(destination=BytesIO())
        filename = "glitch.mp4"
    elif message.content_type == ContentType.AUDIO:
        file_bytes = await message.audio.download(destination=BytesIO())
        filename = "glitch.mp3"
    elif message.content_type == ContentType.DOCUMENT:
        file_bytes = await message.document.download(destination=BytesIO())
        ext = os.path.splitext(message.document.file_name)[-1] or ".txt"
        filename = f"glitch{ext}"
    elif message.content_type == ContentType.ANIMATION:  # GIF
        file_bytes = await message.animation.download(destination=BytesIO())
        filename = "glitch.gif"
    else:
        await message.reply("File type not supported.")
        return

    if random.random() < 0.05:  # 5% chance infinite load
        await message.reply("Upload seems stuck... 🔄")
        while True: await asyncio.sleep(999)

    file_bytes.seek(0)
    data = file_bytes.read()

    # Escalating glitch for all types
    variants = escalating_glitch(data, steps=3)
    for i, v in enumerate(variants):
        bio = BytesIO(v)
        bio.name = filename.replace(".", f"_glitch{i+1}.")
        bio.seek(0)
        await message.answer_document(bio)
        await asyncio.sleep(0.5 + random.random())

# ---------------- Run ----------------
if __name__ == "__main__":
    asyncio.run(dp.start_polling(bot))
