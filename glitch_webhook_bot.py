import os
import random
import asyncio
from io import BytesIO

from aiogram import Bot, Dispatcher, types
from aiogram.filters import Command
from aiogram.types import InputFile, ContentType
from PIL import Image
import numpy as np
from moviepy.editor import VideoFileClip, vfx

TOKEN = os.environ.get("TELEGRAM_TOKEN")
WEBHOOK_URL = os.environ.get("WEBHOOK_URL")  # your Render URL
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

def glitch_video_clip(clip: VideoFileClip, factor=0.1):
    # Simple visual glitch effect: mirror + color shift
    return clip.fx(vfx.lum_contrast, lum=random.randint(-50,50))

def escalating_glitch(data: bytes, steps=3):
    return [glitch_bytes(data, level=(i+1)/steps/2) for i in range(steps)]

# ---------------- Bot Handlers ----------------
@dp.message(Command("start"))
async def start_cmd(message: types.Message):
    await message.reply(
        "Hi! In Russia, messenger and social media blocks are nothing surprising. "
        "Let's turn Telegram slowdowns into art! Upload a file and get glitched versions!"
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

    # --- Apply glitches ---
    if message.content_type in [ContentType.PHOTO, ContentType.ANIMATION]:
        img = Image.open(BytesIO(data))
        steps = 3
        for i in range(steps):
            glitched = glitch_image(img, level=(i+1)/steps*0.1)
            bio = BytesIO()
            ext = ".gif" if message.content_type == ContentType.ANIMATION else ".jpg"
            glitched.save(bio, format="GIF" if ext==".gif" else "JPEG")
            bio.name = f"glitch{i+1}{ext}"
            bio.seek(0)
            await message.answer_document(InputFile(bio, filename=bio.name))
            await asyncio.sleep(0.5 + random.random())
    elif message.content_type == ContentType.VIDEO:
        with open("temp_video.mp4", "wb") as f:
            f.write(data)
        clip = VideoFileClip("temp_video.mp4")
        steps = 2
        for i in range(steps):
            glitched_clip = glitch_video_clip(clip, factor=(i+1)/steps*0.2)
            out_name = f"glitch{i+1}.mp4"
            glitched_clip.write_videofile(out_name, codec="libx264", audio_codec="aac", verbose=False, logger=None)
            await message.answer_document(InputFile(out_name))
            await asyncio.sleep(0.5 + random.random())
    else:
        # Other files: simple byte glitch
        variants = escalating_glitch(data, steps=3)
        for i, v in enumerate(variants):
            bio = BytesIO(v)
            bio.name = f"glitch{i+1}{os.path.splitext(filename)[-1]}"
            bio.seek(0)
            await message.answer_document(InputFile(bio, filename=bio.name))
            await asyncio.sleep(0.5 + random.random())

# ---------------- Run bot ----------------
if __name__ == "__main__":
    import asyncio
    asyncio.run(dp.start_polling(bot))
