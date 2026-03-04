# glitch_polling_bot_env.py
import os
import asyncio
import random
import io
from PIL import Image, ImageChops, ImageEnhance
from moviepy.editor import VideoFileClip, vfx
from aiogram import Bot, Dispatcher, types

# --- BOT TOKEN FROM ENVIRONMENT ---
API_TOKEN = os.environ.get("BOT_TOKEN")
if not API_TOKEN:
    raise ValueError("Please set BOT_TOKEN environment variable!")

bot = Bot(token=API_TOKEN)
dp = Dispatcher()

# --- PROGRESSIVE GLITCH STORAGE ---
# This keeps track of how “glitched” a file has become over multiple uploads
glitch_counts = {}

# === IMAGE GLITCH FUNCTION ===
def glitch_image(img_bytes, intensity=1):
    try:
        img = Image.open(io.BytesIO(img_bytes))
    except Exception:
        return img_bytes

    # Increase color distortion with intensity
    enhancer = ImageEnhance.Color(img)
    img = enhancer.enhance(1 + 0.1 * intensity)

    # Random crop/offset
    if random.random() < 0.7:
        w, h = img.size
        crop_h = int(h * random.uniform(0.85, 0.95))
        img = ImageChops.offset(img.crop((0, 0, w, crop_h)), random.randint(-10, 10), random.randint(-10, 10))

    # Save to bytes
    out_bytes = io.BytesIO()
    img.save(out_bytes, format="PNG")
    out_bytes.seek(0)
    return out_bytes

# === VIDEO GLITCH FUNCTION ===
def glitch_video(video_bytes, intensity=1):
    try:
        with open("temp_input.mp4", "wb") as f:
            f.write(video_bytes)
        clip = VideoFileClip("temp_input.mp4")
    except Exception:
        return video_bytes

    # Color shift and slight speed variations
    clip = clip.fx(vfx.colorx, 1 + 0.05*intensity)
    if random.random() < 0.5:
        clip = clip.fx(vfx.speedx, 0.95 + 0.1*random.random())

    clip.write_videofile("temp_output.mp4", codec="libx264", audio_codec="aac", verbose=False, logger=None)
    with open("temp_output.mp4", "rb") as f:
        out_bytes = io.BytesIO(f.read())
    out_bytes.seek(0)
    return out_bytes

# === FILE HANDLER ===
@dp.message_handler(content_types=types.ContentTypes.ANY)
async def handle_file(message: types.Message):
    file_id = None
    file_bytes = None
    output_bytes = None
    file_name = None

    # Determine which file type
    if message.photo:
        file_id = message.photo[-1].file_id
        file_name = "glitched.png"
    elif message.video:
        file_id = message.video.file_id
        file_name = "glitched.mp4"
    elif message.document:
        file_id = message.document.file_id
        file_name = "glitched_" + message.document.file_name
    else:
        await message.reply("Hi! In Russia, messenger and social media blocks are nothing surprising. "
        "Let's turn Telegram slowdowns into art -- Upload a file and get glitched versions")
        return

    # Download file
    file_info = await bot.get_file(file_id)
    file_bytes_io = await bot.download_file(file_info.file_path)
    file_bytes = file_bytes_io.read()

    # Track glitch intensity
    count = glitch_counts.get(file_id, 0) + 1
    glitch_counts[file_id] = count
    intensity = min(count, 10)  # cap intensity

    # Glitch according to type
    if message.photo:
        output_bytes = glitch_image(file_bytes, intensity)
    elif message.video:
        output_bytes = glitch_video(file_bytes, intensity)
    elif message.document:
        # Randomly corrupt bytes
        b = bytearray(file_bytes)
        for i in range(0, len(b), max(1000, len(b)//100)):
            if random.random() < 0.1 * intensity:
                b[i] = b[i] ^ random.randint(0,255)
        output_bytes = io.BytesIO(b)
        output_bytes.seek(0)

    # Send glitched file back
    await message.reply_document(types.InputFile(output_bytes, filename=file_name))

# === START BOT ===
async def main():
    print("Bot started. Listening for files...")
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
