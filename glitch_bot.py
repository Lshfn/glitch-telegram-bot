import os
import random
import asyncio
from io import BytesIO
from PIL import Image
from aiogram import Bot, Dispatcher, types

BOT_TOKEN = os.getenv("BOT_TOKEN")

bot = Bot(token=BOT_TOKEN)
dp = Dispatcher()

glitch_levels = {}

# ----------------
# BYTE CHAOS
# ----------------

def corrupt_bytes(data,intensity):

    data = bytearray(data)

    changes = int(len(data)*0.003*intensity)

    for _ in range(changes):

        i = random.randint(0,len(data)-1)
        data[i] = random.randint(0,255)

    return bytes(data)

# ----------------
# RGB SHIFT
# ----------------

def rgb_shift(img):

    r,g,b = img.split()

    r = r.transform(r.size,Image.AFFINE,(1,0,random.randint(-5,5),0,1,0))
    b = b.transform(b.size,Image.AFFINE,(1,0,random.randint(5,10),0,1,0))

    return Image.merge("RGB",(r,g,b))

# ----------------
# PIXEL SORT
# ----------------

def pixel_sort(img):

    pixels = list(img.getdata())

    step = random.randint(200,800)

    for i in range(0,len(pixels),step):

        block = pixels[i:i+step]
        block.sort(key=lambda x: sum(x))

        pixels[i:i+step] = block

    img.putdata(pixels)

    return img

# ----------------
# VHS LINES
# ----------------

def vhs_lines(img):

    pixels = img.load()

    for y in range(0,img.height,random.randint(4,12)):

        shift = random.randint(-30,30)

        for x in range(img.width):

            nx = (x+shift) % img.width
            pixels[x,y] = pixels[nx,y]

    return img

# ----------------
# CRT DISTORTION
# ----------------

def crt_distortion(img):

    pixels = img.load()

    for y in range(img.height):

        wave = int(10 * random.random() * random.sin(y/20))

        for x in range(img.width):

            nx = (x+wave) % img.width
            pixels[x,y] = pixels[nx,y]

    return img

# ----------------
# FRACTAL CHAOS
# ----------------

def fractal_noise(img,intensity):

    pixels = img.load()

    amount = 1000 * intensity

    for _ in range(amount):

        x = random.randint(0,img.width-1)
        y = random.randint(0,img.height-1)

        pixels[x,y] = (
            random.randint(0,255),
            random.randint(0,255),
            random.randint(0,255)
        )

    return img

# ----------------
# IMAGE GLITCH
# ----------------

def glitch_image(data,intensity):

    img = Image.open(BytesIO(data)).convert("RGB")

    img = rgb_shift(img)
    img = pixel_sort(img)
    img = vhs_lines(img)
    img = fractal_noise(img,intensity)

    buf = BytesIO()
    img.save(buf,"PNG")

    return buf.getvalue()

# ----------------
# JPEG DEEP GLITCH
# ----------------

def jpeg_glitch(data,intensity):

    data = bytearray(data)

    start = int(len(data)*0.1)
    end = int(len(data)*0.8)

    for _ in range(200*intensity):

        i = random.randint(start,end)
        data[i] = random.randint(0,255)

    return bytes(data)

# ----------------
# ROUTER
# ----------------

def glitch_router(data,name,intensity):

    name = name.lower()

    if name.endswith(".jpg") or name.endswith(".jpeg"):
        return jpeg_glitch(data,intensity)

    if name.endswith(".png"):
        return glitch_image(data,intensity)

    if name.endswith(".gif") or name.endswith(".mp4"):
        return corrupt_bytes(data,intensity)

    return corrupt_bytes(data,intensity)

# ----------------
# TELEGRAM HANDLER
# ----------------

@dp.message()
async def handle_file(message: types.Message):

    file_id = None
    filename = "file.bin"

    if message.photo:
        file_id = message.photo[-1].file_id
        filename = "image.jpg"

    elif message.video:
        file_id = message.video.file_id
        filename = "video.mp4"

    elif message.document:
        file_id = message.document.file_id
        filename = message.document.file_name

    if not file_id:

        await message.answer("Hey. Here, in Russia, messenger and social media blocks are nothing surprising. Let's turn Telegram slowdowns into art. Upload a file and get glitched versions")
        return

    file = await bot.get_file(file_id)
    file_data = await bot.download_file(file.file_path)

    data = file_data.read()

    level = glitch_levels.get(file_id,1)
    glitch_levels[file_id] = level + 1

    result = glitch_router(data,filename,level)

    buf = BytesIO(result)
    buf.name = "glitched_" + filename

    await message.reply_document(buf)

# ----------------
# START
# ----------------

async def main():

    print("ULTRA CHAOS GLITCH BOT")

    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
