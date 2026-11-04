import os
import random
import asyncio
import math
import logging
import weakref
from contextlib import suppress
from io import BytesIO

from PIL import Image, ImageChops, ImageEnhance, ImageFilter, ImageOps
from aiogram import Bot, Dispatcher, types
from aiogram.client.session.aiohttp import AiohttpSession
from aiogram.client.telegram import TelegramAPIServer
from aiogram.types import BufferedInputFile
from aiogram.exceptions import TelegramForbiddenError, TelegramBadRequest, TelegramRetryAfter
from aiogram.webhook.aiohttp_server import SimpleRequestHandler, setup_application
from aiohttp import web
import aiohttp

from artifact_memory import configured_memory, open_image
from language_glitch import Translator, TEXT_EXTENSIONS, decode_text, read_regions
from memory_effects import inherit_image, inherit_bytes, glitch_text
from image_agents import agent_canvas
from image_surfaces import content_mask
from content_fusion import trace, merge_traces, fuse_image, fuse_text, fuse_bytes
from file_formats import CONTENT_EXTENSIONS, ContentLimitError, ContentUnavailable, run_content_job
from bot_settings import MAX_UPLOAD, MAX_UPLOAD_MB, MAX_OUTPUT, BOT_API_URL, BOT_API_LOCAL_FILES
from bot_settings import MEMORY_DREAMS
from memory_dreams import make_dream
from dream_auth import authorize_dream_tick
from jwt import PyJWTError

BOT_TOKEN = os.getenv("BOT_TOKEN")

def make_bot(token):
    options = {"timeout": 180}
    if BOT_API_URL:
        options["api"] = TelegramAPIServer.from_base(BOT_API_URL, is_local=BOT_API_LOCAL_FILES)
    return Bot(token=token, session=AiohttpSession(**options))


class UploadBuffer(BytesIO):
    """Enforce the byte limit while downloading, including absent size metadata."""
    def write(self, data):
        if self.tell() + len(data) > MAX_UPLOAD:
            raise ContentLimitError(f"File exceeds {MAX_UPLOAD_MB} MB.")
        return super().write(data)


bot = make_bot(BOT_TOKEN) if BOT_TOKEN else None
dp = Dispatcher()

# Sender-local history; remote storage can survive free-host restarts.
memory = configured_memory()
translator = Translator(os.getenv("TRANSLATE_URL", ""), os.getenv("TRANSLATE_API_KEY", ""),
                        [s.strip() for s in os.getenv("TRANSLATE_TARGETS", "en,ru,de,fr,es,it,pt,el,uk,pl,tr").split(",") if s.strip()])
processing_slots = asyncio.Semaphore(2)
content_slots = asyncio.Semaphore(1)
dream_dispatch_lock = asyncio.Lock()
chat_locks = weakref.WeakValueDictionary()
logger = logging.getLogger(__name__)


def chat_lock(chat):
    lock = chat_locks.get(chat)
    if lock is None:
        lock = asyncio.Lock()
        chat_locks[chat] = lock
    return lock

# Keep processing expressive without allowing accidental gigantic loops.
MAX_INTENSITY = 12


def automatic_intensity(repeat_level):
    """Allow quiet returns even after a violent render; repeats only bias odds."""
    pressure = min(1, max(0, repeat_level - 1) / 11)
    weights = (2, 3, 4, 5, 5, 4, 3, 2.5, 2, 1.5, 1, .8)
    return random.choices(range(1, MAX_INTENSITY + 1),
                          [w * (1 + pressure * i / 6) for i, w in enumerate(weights)])[0]


# ---------------------------------------------------------------------------
# GENERIC BYTE CHAOS (GIF / MP4 / unknown files)
# ---------------------------------------------------------------------------
def corrupt_bytes(data, intensity):
    """Corrupt payload bytes using a random mixture of mutation styles.

    We avoid the beginning/end of the file so headers and trailers have a better
    chance of surviving. This is still intentionally destructive for formats we
    do not decode ourselves.
    """
    if len(data) < 64:
        return data

    level = min(max(intensity, 1), MAX_INTENSITY)
    buf = bytearray(data)

    start = max(16, int(len(buf) * random.uniform(0.06, 0.18)))
    end = min(len(buf) - 8, int(len(buf) * random.uniform(0.82, 0.96)))
    if end <= start:
        return bytes(buf)

    bursts = random.randint(2, 4 + level * 2)

    for _ in range(bursts):
        mode = random.choice(("noise", "xor", "repeat", "zero", "swap"))
        pos = random.randint(start, end - 1)
        max_span = max(1, min(end - pos, 4 + level * 12))
        span = random.randint(1, max_span)

        if mode == "noise":
            for i in range(pos, pos + span):
                buf[i] = random.randrange(256)

        elif mode == "xor":
            mask = random.randrange(1, 256)
            for i in range(pos, pos + span):
                buf[i] ^= mask

        elif mode == "repeat":
            source = random.randint(start, max(start, end - span))
            buf[pos:pos + span] = buf[source:source + span]

        elif mode == "zero":
            value = random.choice((0x00, 0xFF, random.randrange(256)))
            buf[pos:pos + span] = bytes([value]) * span

        elif mode == "swap" and span > 1:
            other = random.randint(start, max(start, end - span))
            a = bytes(buf[pos:pos + span])
            b = bytes(buf[other:other + span])
            buf[pos:pos + span] = b
            buf[other:other + span] = a

    return bytes(buf)


# ---------------------------------------------------------------------------
# IMAGE GLITCH EFFECTS
# ---------------------------------------------------------------------------
def _shift_channel(channel, dx=0, dy=0):
    return ImageChops.offset(channel, dx, dy)


def rgb_shift(img, strength):
    """Independent horizontal/vertical displacement of RGB channels."""
    r, g, b = img.split()
    max_x = max(2, int(img.width * (0.003 + 0.018 * strength)))
    max_y = max(1, int(img.height * (0.001 + 0.008 * strength)))

    shifts = []
    for channel in (r, g, b):
        dx = random.randint(-max_x, max_x)
        dy = random.randint(-max_y, max_y) if random.random() < 0.45 else 0
        shifts.append(_shift_channel(channel, dx, dy))

    if random.random() < 0.22 + strength * 0.35:
        random.shuffle(shifts)

    return Image.merge("RGB", tuple(shifts))


def band_shift(img, strength):
    """Move irregular horizontal strips independently."""
    out = img.copy()
    count = random.randint(1, 2 + int(strength * 5))
    max_shift = max(2, int(img.width * (0.01 + strength * 0.08)))

    for _ in range(count):
        h = random.randint(
            1,
            max(1, img.height // 70),
        )
        y = random.randint(0, max(0, img.height - h))
        strip = out.crop((0, y, img.width, y + h))

        dx = random.randint(-max_shift, max_shift)
        strip = ImageChops.offset(strip, dx, 0)

        if random.random() < 0.2:
            strip = ImageOps.mirror(strip)
        if random.random() < 0.12:
            strip = ImageOps.invert(strip)

        out.paste(strip, (0, y))

    return out


def block_displace(img, strength):
    """Duplicate, displace, mirror and occasionally invert rectangular regions."""
    out = img.copy()
    count = random.randint(1, 2 + int(strength * 6))

    for _ in range(count):
        bw = random.randint(max(4, img.width // 30), max(5, img.width // 3))
        bh = random.randint(max(3, img.height // 40), max(4, img.height // 3))
        x = random.randint(0, max(0, img.width - bw))
        y = random.randint(0, max(0, img.height - bh))
        block = out.crop((x, y, x + bw, y + bh))

        if random.random() < 0.25:
            block = ImageOps.mirror(block)
        if random.random() < 0.12 + strength * 0.2:
            block = ImageOps.flip(block)
        if random.random() < 0.08 + strength * 0.15:
            block = ImageOps.invert(block)

        max_dx = max(3, int(img.width * (0.03 + 0.22 * strength)))
        max_dy = max(2, int(img.height * (0.02 + 0.12 * strength)))
        nx = max(0, min(img.width - bw, x + random.randint(-max_dx, max_dx)))
        ny = max(0, min(img.height - bh, y + random.randint(-max_dy, max_dy)))
        out.paste(block, (nx, ny))

    return out


def channel_blocks(img, strength):
    """Swap/offset channels only inside selected blocks."""
    out = img.copy()
    count = random.randint(1, 2 + int(strength * 9))

    for _ in range(count):
        bw = random.randint(max(8, img.width // 16), max(9, img.width // 2))
        bh = random.randint(max(6, img.height // 24), max(7, img.height // 3))
        x = random.randint(0, max(0, img.width - bw))
        y = random.randint(0, max(0, img.height - bh))
        block = out.crop((x, y, x + bw, y + bh))
        r, g, b = block.split()
        channels = [r, g, b]
        random.shuffle(channels)

        max_shift = max(1, int(bw * (0.02 + strength * 0.12)))
        channel_index = random.randrange(3)
        channels[channel_index] = ImageChops.offset(
            channels[channel_index], random.randint(-max_shift, max_shift), 0
        )
        block = Image.merge("RGB", tuple(channels))
        out.paste(block, (x, y))

    return out


def wave_warp(img, strength):
    """Horizontal sinusoidal displacement with variable frequency and phase."""
    out = Image.new("RGB", img.size)
    strip_h = random.randint(1, max(2, int(3 + 8 * (1 - strength))))
    amplitude = max(1, int(img.width * random.uniform(0.005, 0.01 + 0.04 * strength)))
    wavelength = random.uniform(12, 90 - min(60, strength * 45))
    phase = random.uniform(0, math.tau)

    for y in range(0, img.height, strip_h):
        h = min(strip_h, img.height - y)
        strip = img.crop((0, y, img.width, y + h))
        dx = int(amplitude * math.sin((y / wavelength) + phase))
        out.paste(ImageChops.offset(strip, dx, 0), (0, y))

    return out


def scanline_dropout(img, strength):
    """Create analog-looking scanline dropouts, streaks and small slices."""
    out = img.copy()
    count = random.randint(1, 3 + int(strength * 5))

    for _ in range(count):
        y = random.randrange(img.height)
        h = random.randint(1, max(1, int(1 + strength * 2)))
        h = min(h, img.height - y)
        if h <= 0:
            continue

        line = out.crop((0, y, img.width, y + h))
        mode = random.choice(("dark", "bright", "invert", "offset", "mono"))

        if mode == "dark":
            line = ImageEnhance.Brightness(line).enhance(random.uniform(0.05, 0.55))
        elif mode == "bright":
            line = ImageEnhance.Brightness(line).enhance(random.uniform(1.4, 2.6))
        elif mode == "invert":
            line = ImageOps.invert(line)
        elif mode == "offset":
            line = ImageChops.offset(
                line, random.randint(-max(2, img.width // 5), max(2, img.width // 5)), 0
            )
        elif mode == "mono":
            gray = ImageOps.grayscale(line)
            line = Image.merge("RGB", (gray, gray, gray))

        out.paste(line, (0, y))

    return out


def pixel_sort_bands(img, strength):
    """Sort selected row segments instead of sorting the entire image uniformly."""
    out = img.copy()
    px = out.load()
    rows = random.randint(3, 8 + int(strength * 24))

    for _ in range(rows):
        y = random.randrange(img.height)
        x1 = random.randrange(max(1, img.width - 1))
        min_len = max(8, img.width // 20)
        max_len = max(min_len, int(img.width * random.uniform(0.15, 0.75)))
        x2 = min(img.width, x1 + random.randint(min_len, max_len))
        if x2 - x1 < 2:
            continue

        segment = [px[x, y] for x in range(x1, x2)]
        key = random.choice((
            lambda c: sum(c),
            lambda c: c[0],
            lambda c: c[1],
            lambda c: c[2],
            lambda c: max(c) - min(c),
        ))
        segment.sort(key=key, reverse=random.random() < 0.5)

        # Sometimes rotate the sorted segment so the effect is less "textbook".
        if random.random() < 0.45 and segment:
            cut = random.randrange(len(segment))
            segment = segment[cut:] + segment[:cut]

        for i, color in enumerate(segment):
            px[x1 + i, y] = color

        # Smear the row into a small vertical band.
        if random.random() < 0.55:
            band_h = random.randint(1, max(1, int(1 + strength * 7)))
            source = out.crop((x1, y, x2, y + 1))
            for yy in range(y + 1, min(img.height, y + band_h)):
                out.paste(source, (x1, yy))

    return out


def noise_blocks(img, strength):
    """Add clustered digital noise instead of evenly distributed single pixels."""
    out = img.copy()
    px = out.load()
    clusters = random.randint(2, 5 + int(strength * 16))

    for _ in range(clusters):
        cx = random.randrange(img.width)
        cy = random.randrange(img.height)
        radius_x = random.randint(2, max(3, int(img.width * (0.005 + 0.035 * strength))))
        radius_y = random.randint(1, max(2, int(img.height * (0.004 + 0.025 * strength))))
        mode = random.choice(("noise", "xor", "white", "black"))

        for y in range(max(0, cy - radius_y), min(img.height, cy + radius_y + 1)):
            for x in range(max(0, cx - radius_x), min(img.width, cx + radius_x + 1)):
                if random.random() > random.uniform(0.25, 0.82):
                    continue
                r, g, b = px[x, y]
                if mode == "noise":
                    px[x, y] = (random.randrange(256), random.randrange(256), random.randrange(256))
                elif mode == "xor":
                    mask = random.randrange(32, 256)
                    px[x, y] = (r ^ mask, g ^ mask, b ^ mask)
                elif mode == "white":
                    v = random.randrange(180, 256)
                    px[x, y] = (v, v, v)
                else:
                    v = random.randrange(0, 65)
                    px[x, y] = (v, v, v)

    return out


def feedback_echo(img, strength):
    """Blend several offset copies to create recursive/feedback-like echoes."""
    out = img.copy()
    echoes = random.randint(1, 2 + int(strength * 5))
    max_dx = max(2, int(img.width * (0.015 + strength * 0.08)))
    max_dy = max(1, int(img.height * (0.01 + strength * 0.05)))

    for _ in range(echoes):
        shifted = ImageChops.offset(
            out,
            random.randint(-max_dx, max_dx),
            random.randint(-max_dy, max_dy),
        )
        alpha = random.uniform(0.12, 0.38)
        out = Image.blend(out, shifted, alpha)

    return out


def compression_ghost(img, strength):
    """Introduce JPEG blocks/ghosting while keeping the final image valid."""
    quality_min = max(8, int(42 - strength * 28))
    quality_max = max(quality_min + 1, int(72 - strength * 24))
    quality = random.randint(quality_min, quality_max)

    buf = BytesIO()
    img.save(buf, "JPEG", quality=quality, optimize=False)
    buf.seek(0)
    compressed = Image.open(buf).convert("RGB")

    if random.random() < 0.55:
        dx = random.randint(-max(1, img.width // 60), max(1, img.width // 60))
        compressed = ImageChops.offset(compressed, dx, 0)
        compressed = Image.blend(img, compressed, random.uniform(0.35, 0.8))

    return compressed


def palette_crush(img, strength):
    """Occasional posterization / contrast break-up."""
    bits = random.choice((2, 3, 4, 5))
    crushed = ImageOps.posterize(img, bits)
    if random.random() < 0.45:
        crushed = ImageEnhance.Contrast(crushed).enhance(random.uniform(1.2, 2.3))
    return Image.blend(img, crushed, random.uniform(0.25, 0.72))


def surface_erosion(img, strength):
    """Let dark/light picture detail spread into neighbouring texture."""
    operation = random.choice((ImageFilter.MinFilter(3), ImageFilter.MaxFilter(3)))
    eroded = img.filter(operation).filter(ImageFilter.UnsharpMask(radius=1.5, percent=130, threshold=2))
    return Image.blend(img, eroded, .2 + strength * .6)


EFFECTS = {
    "rgb split": rgb_shift,
    "band drift": band_shift,
    "block displacement": block_displace,
    "channel blocks": channel_blocks,
    "wave warp": wave_warp,
    "scanline dropout": scanline_dropout,
    "pixel sort": pixel_sort_bands,
    "noise clusters": noise_blocks,
    "feedback echo": feedback_echo,
    "compression ghosts": compression_ghost,
    "palette crush": palette_crush,
    "surface erosion": surface_erosion,
}


# Weights make some effects common and others feel like rarer surprises.
EFFECT_WEIGHTS = {
    "rgb split": 1.25,
    "band drift": 0.15,
    "block displacement": 0.65,
    "channel blocks": 0.85,
    "wave warp": 0.18,
    "scanline dropout": 0.12,
    "pixel sort": 0.28,
    "noise clusters": 0.45,
    "feedback echo": 0.85,
    "compression ghosts": 0.75,
    "palette crush": 0.45,
    "surface erosion": 1.0,
}
STRIP_EFFECTS = {"band drift", "wave warp", "scanline dropout", "pixel sort"}
AUTOMATIC_EFFECTS = ("rgb split", "surface erosion", "feedback echo", "compression ghosts", "palette crush")


def _weighted_unique_effects(count):
    names = list(AUTOMATIC_EFFECTS)
    picked = []

    while names and len(picked) < count:
        weights = [EFFECT_WEIGHTS[name] for name in names]
        choice = random.choices(names, weights=weights, k=1)[0]
        picked.append(choice)
        names.remove(choice)
        if choice in STRIP_EFFECTS:
            names = [name for name in names if name not in STRIP_EFFECTS]

    return picked


def make_glitch_recipe(intensity):
    """Build a different recipe for every render."""
    level = min(max(intensity, 1), MAX_INTENSITY)
    strength = random.uniform(.035 + level * .012, .15 + level * .055)
    count = random.randint(1, 2 + level // 4)

    recipe = _weighted_unique_effects(count)
    random.shuffle(recipe)

    if level >= 8 and random.random() < 0.12:
        # Repeated strokes are occasional; a strip is never repeated.
        repeatable = [name for name in recipe if name not in STRIP_EFFECTS]
        if repeatable:
            recipe.insert(random.randrange(len(recipe) + 1), random.choice(repeatable))

    return recipe, strength


def jpeg_databend(data, strength):
    """Bend JPEG scan data, but keep the original if Pillow can no longer decode it.

    This preserves the chaotic JPEG-artifact character of the old bot without
    blindly returning an unreadable file every time.
    """
    buf = bytearray(data)
    sos = buf.find(b"\xff\xda")
    eoi = buf.rfind(b"\xff\xd9")

    if sos < 0 or eoi < 0 or sos + 4 >= eoi:
        return data

    segment_length = int.from_bytes(buf[sos + 2:sos + 4], "big")
    start = sos + 2 + segment_length
    end = eoi
    if start >= end - 4:
        return data

    original = bytes(buf)
    bursts = random.randint(1, 2 + int(strength * 12))

    for _ in range(bursts):
        pos = random.randint(start, end - 2)
        span = random.randint(1, max(1, int(2 + strength * 10)))
        mask = random.randrange(1, 255)

        for i in range(pos, min(end, pos + span)):
            # Avoid touching marker/stuffing bytes.
            if buf[i] == 0xFF or (i > 0 and buf[i - 1] == 0xFF):
                continue
            value = buf[i] ^ mask
            if value == 0xFF:
                value = 0xFE
            buf[i] = value

    bent = bytes(buf)
    try:
        test = Image.open(BytesIO(bent))
        test.load()
        return bent
    except Exception:
        return original


def glitch_image(data, intensity, output_format="PNG", artifacts=(), prepared=None, source=None, fusion=True):
    img = prepared.copy() if prepared is not None else open_image(data)
    original = img.copy()
    if fusion and source is None:
        source = trace(data, image=img)
    recipe, strength = make_glitch_recipe(intensity)

    for name in recipe:
        # Each stage receives slightly different strength so two runs with the
        # same intensity do not collapse into the same visual grammar.
        stage_strength = min(1.0, max(0.05, strength * random.uniform(0.65, 1.35)))
        changed = EFFECTS[name](img, stage_strength)
        mask = content_mask(original, changed, random.uniform(.12, .4))
        img = Image.composite(changed, img, mask)

    if fusion:
        img, mathematics = fuse_image(img, source, artifacts, intensity)
        recipe.extend(mathematics)
        if random.random() < .38:
            img, agents = agent_canvas(img, original, artifacts, intensity)
            recipe.extend(agents)

    # Memory arrives last, so the selected scene/words can still be recognised.
    img, inherited = inherit_image(img, artifacts, strength)
    recipe.extend(inherited)

    buf = BytesIO()
    if output_format.upper() == "JPEG":
        img.save(buf, "JPEG", quality=random.randint(72, 94), subsampling=random.choice((0, 1, 2)))
        result = buf.getvalue()
        if not fusion and random.random() < 0.04 + strength * 0.13:
            bent = jpeg_databend(result, strength)
            if bent != result:
                result = bent
                recipe.append("jpeg databend")
        return result, recipe

    img.save(buf, "PNG", optimize=False)
    return buf.getvalue(), recipe


# ---------------------------------------------------------------------------
# ROUTER
# ---------------------------------------------------------------------------
def glitch_router(data, name, intensity, artifacts=(), fusion=True):
    name = (name or "file.bin").lower()

    if name.endswith((".jpg", ".jpeg")):
        return glitch_image(data, intensity, "JPEG", artifacts, fusion=fusion)

    if name.endswith(".png"):
        return glitch_image(data, intensity, "PNG", artifacts, fusion=fusion)

    data, inherited = inherit_bytes(data, artifacts, intensity)
    result = corrupt_bytes(data, intensity)
    recipe = inherited + ["byte chaos"]
    if fusion:
        result, maths = fuse_bytes(result, trace(data), artifacts, intensity)
        recipe.extend(maths)
    return result, recipe


async def process_upload(data, filename, level, artifacts, translate=False, mode="fuse"):
    name = filename.lower()
    notices, translated, failures = [], [], 0
    artifact_text = ""
    source = None
    image = None
    fusion = mode != "raw"
    is_image = name.endswith((".jpg", ".jpeg", ".png", ".webp", ".bmp", ".tif", ".tiff"))
    if is_image:
        image = await asyncio.to_thread(open_image, data)
        original_image = image.copy()
        if translate and translator.configured:
            try:
                regions = await asyncio.to_thread(read_regions, image)
                artifact_text = " ".join(r.text for r in regions)
                async with aiohttp.ClientSession() as session:
                    async with asyncio.timeout(24):
                        image, translated, failures = await translator.replace_regions(image, regions, session)
                if not regions:
                    notices.append("No readable text detected in this image.")
            except (RuntimeError, ImportError, asyncio.TimeoutError) as exc:
                notices.append("Image translation unavailable (OCR setup or time limit).")
                logger.warning("OCR/translation skipped: %s", type(exc).__name__)
        output_format = "JPEG" if name.endswith((".jpg", ".jpeg")) else "PNG"
        source = await asyncio.to_thread(trace, data, artifact_text, original_image)
        result, recipe = await asyncio.to_thread(glitch_image, data, level, output_format, artifacts, image, source, fusion)
        if output_format == "PNG" and not name.endswith(".png"):
            filename = os.path.splitext(filename)[0] + ".png"
    elif name.endswith(TEXT_EXTENSIONS):
        text, _encoding = decode_text(data)
        source = await asyncio.to_thread(trace, data, text)
        if translate and translator.configured:
            try:
                async with aiohttp.ClientSession() as session:
                    async with asyncio.timeout(24):
                        text, translated, failures = await translator.replace_spans(text, session)
            except asyncio.TimeoutError:
                notices.append("Translation time limit reached; text glitches still applied.")
        recipe = []
        if fusion:
            text, maths = await asyncio.to_thread(fuse_text, text, source, artifacts, level)
            recipe.extend(maths)
        text, inherited = await asyncio.to_thread(glitch_text, text, artifacts, level)
        recipe.extend(inherited)
        artifact_text = text
        result = text.encode("utf-8")
    elif fusion and name.endswith(CONTENT_EXTENSIONS):
        try:
            async with content_slots:
                result, recipe, artifact, format_notices, extension = await asyncio.to_thread(
                    run_content_job, data, os.path.splitext(name)[1], level, artifacts)
            filename = os.path.splitext(filename)[0] + extension
            return result, recipe, artifact, format_notices, filename
        except ContentUnavailable as exc:
            logger.warning("Content fusion unavailable: %s", exc)
            notices.append("Content decoding unavailable; used destructive byte chaos. Check the Docker decoder setup.")
            result, recipe = await asyncio.to_thread(glitch_router, data, filename, level, artifacts, fusion)
    else:
        result, recipe = await asyncio.to_thread(glitch_router, data, filename, level, artifacts, fusion)
    if translated:
        recipe.append("translations: " + ", ".join(translated))
    if failures:
        notices.append("Some translations failed; remaining glitches still applied.")
    if source is None:
        source = await asyncio.to_thread(trace, data)
    rendered_image = await asyncio.to_thread(open_image, result) if is_image else None
    artifact = await asyncio.to_thread(trace, result, artifact_text, rendered_image)
    artifact = await asyncio.to_thread(merge_traces, source, artifact)
    return result, recipe, artifact, notices, filename


# ---------------------------------------------------------------------------
# TELEGRAM HANDLER
# ---------------------------------------------------------------------------
@dp.message()
async def handle_file(message: types.Message):
    # A private history per sender AND chat, including in group conversations.
    scope = f"{message.chat.id}:{message.from_user.id if message.from_user else 0}"
    command = (message.text or "").split()
    action = command[0].split("@")[0].lower() if command else ""
    async with chat_lock(scope):
        if action in ("/start", "/help"):
            await message.answer("✶")
            return
        if action == "/forget":
            await asyncio.to_thread(memory.forget, scope)
            await message.answer("Memory cleared.")
            return
        if action == "/dreams":
            option = command[1].lower() if len(command) > 1 else ""
            if getattr(message.chat, "type", "") == "private" and option in ("on", "off"):
                await asyncio.to_thread(memory.schedule_dream, scope, option == "on")
                await message.answer("✶")
            return
        if action == "/translate":
            option = command[1].lower() if len(command) > 1 else ""
            if option not in ("on", "off"):
                return
            if option == "on" and not translator.configured:
                await message.answer("Translation unavailable.")
                return
            await asyncio.to_thread(memory.translations, scope, option == "on")
            await message.answer("Translation on." if option == "on" else "Translation off.")
            return

        upload = (message.photo[-1] if message.photo else message.video or message.audio or
                  message.voice or message.video_note or message.animation or message.document)
        if not upload:
            return
        if upload.file_size and upload.file_size > MAX_UPLOAD:
            await message.answer(f"File exceeds {MAX_UPLOAD_MB} MB.")
            return
        filename = "image.jpg" if message.photo else getattr(upload, "file_name", None)
        if not filename:
            filename = ("voice.ogg" if message.voice else "audio.mp3" if message.audio else
                        "video.mp4" if message.video or message.video_note or message.animation else "file.bin")
        # Strip client-supplied directory components from the reply filename.
        filename = filename.replace("\\", "/").rsplit("/", 1)[-1][:180] or "file.bin"
        async with processing_slots:
            try:
                file = await bot.get_file(upload.file_id)
                stream = await bot.download_file(file.file_path, destination=UploadBuffer(), timeout=120)
                data = stream.read(MAX_UPLOAD + 1)
                stream.close()
                if len(data) > MAX_UPLOAD:
                    await message.answer(f"File exceeds {MAX_UPLOAD_MB} MB.")
                    return
                if filename.lower().endswith(TEXT_EXTENSIONS) and len(data) > 512 * 1024:
                    await message.answer("Text file exceeds 512 KB.")
                    return
                digest = memory.digest(data)
                level, history = await asyncio.to_thread(memory.snapshot, scope, digest)
                enabled = await asyncio.to_thread(memory.translations, scope)
                level = automatic_intensity(level)
                result, recipe, artifact, notices, filename = await process_upload(
                    data, filename, level, history, enabled)
                if len(result) > MAX_OUTPUT:
                    raise ContentLimitError(f"Result exceeds {MAX_OUTPUT // (1024 * 1024)} MB.")
                await message.reply_document(
                    BufferedInputFile(result, filename="glitched_" + filename),
                )
                # Failed processing/delivery never becomes a donor for later files.
                await asyncio.to_thread(memory.remember, scope, digest, level, artifact)
                if MEMORY_DREAMS and getattr(message.chat, "type", "") == "private":
                    await asyncio.to_thread(memory.schedule_dream, scope)
                logger.info("Glitch level=%s effects=%s", level, recipe)
                for notice in notices:
                    logger.info("Processing notice: %s", notice)
            except ContentLimitError as exc:
                await message.answer(str(exc))
            except Exception as exc:
                # Do not log uploaded names, OCR text, file bytes or provider keys.
                logger.warning("Glitch failed: %s", type(exc).__name__)
                await message.answer("The glitch collapsed.")


# ---------------------------------------------------------------------------
# START
# ---------------------------------------------------------------------------
# ---------------------------------------------------------------------------
# START / DEPLOYMENT
# ---------------------------------------------------------------------------
WEBHOOK_PATH = "/telegram-webhook"


async def send_due_dreams():
    if not MEMORY_DREAMS or bot is None or dream_dispatch_lock.locked():
        return 0
    sent = 0
    async with dream_dispatch_lock:
        claims = await asyncio.to_thread(memory.claim_dreams)
        for scope, token in claims:
            delivered, blocked, retry_after = False, False, 0
            async with chat_lock(scope):
                try:
                    async with processing_slots:
                        _, artifacts = await asyncio.to_thread(memory.snapshot, scope, "")
                        if len(artifacts) < 2:
                            continue
                        data, filename = await asyncio.to_thread(make_dream, artifacts)
                        if len(data) > MAX_OUTPUT:
                            raise ContentLimitError("Memory mix exceeds the output limit")
                        if not await asyncio.to_thread(memory.dream_allowed, scope, token):
                            continue
                        await bot.send_document(chat_id=memory.private_recipient(scope),
                                                document=BufferedInputFile(data, filename=filename))
                        delivered = True
                        sent += 1
                except TelegramForbiddenError:
                    blocked = True
                except TelegramRetryAfter as exc:
                    retry_after = exc.retry_after
                except TelegramBadRequest as exc:
                    blocked = any(value in exc.message.lower() for value in ("chat not found", "user is deactivated"))
                    logger.warning("Memory mix delivery failed: %s", type(exc).__name__)
                except Exception as exc:
                    logger.warning("Memory mix failed: %s", type(exc).__name__)
                finally:
                    await asyncio.to_thread(memory.finish_dream, scope, token, delivered, blocked, retry_after)
    return sent


async def dream_loop():
    while True:
        try:
            await send_due_dreams()
        except Exception as exc:
            logger.warning("Memory scheduler unavailable: %s", type(exc).__name__)
        await asyncio.sleep(60)


async def dream_lifecycle(app):
    task = asyncio.create_task(dream_loop())
    try:
        yield
    finally:
        task.cancel()
        with suppress(asyncio.CancelledError):
            await task


async def memory_tick(request):
    # No recipient, file or timing parameters can be supplied by the caller.
    # GitHub signs a short-lived identity for this exact main-branch workflow.
    origin = os.getenv("RENDER_EXTERNAL_URL", "").rstrip("/")
    try:
        await asyncio.to_thread(authorize_dream_tick, request.headers.get("Authorization", ""), origin)
    except (PyJWTError, ValueError):
        return web.Response(status=403, text="Forbidden")
    try:
        await send_due_dreams()
    except Exception as exc:
        logger.warning("Scheduled memory check failed: %s", type(exc).__name__)
        return web.Response(status=503, text="Memory unavailable")
    return web.Response(text="Memory check completed")


async def healthcheck(request):
    """Simple endpoint required by Render web services."""
    return web.Response(text="glitch bot alive")


async def clear_command_menu(bot: Bot):
    """Remove the old default Telegram command menu after deployment."""
    try:
        await bot.delete_my_commands()
    except Exception as exc:
        logger.warning("Command menu cleanup failed: %s", type(exc).__name__)


async def on_render_startup(bot: Bot):
    """Register Telegram webhook when running as a Render web service."""
    base_url = os.getenv("RENDER_EXTERNAL_URL", "").rstrip("/")
    if not base_url:
        raise RuntimeError("RENDER_EXTERNAL_URL is missing on Render")

    await clear_command_menu(bot)
    webhook_url = f"{base_url}{WEBHOOK_PATH}"
    await bot.set_webhook(
        webhook_url,
        allowed_updates=dp.resolve_used_update_types(),
        drop_pending_updates=False,
    )
    print(f"GLITCH BOT WEBHOOK ACTIVE: {webhook_url}")
    logger.info("Memory storage backend=%s", "remote" if memory.remote_url else "local")


async def run_local_polling():
    """Convenient local mode: `python glitch_bot.py` still works off Render."""
    # Telegram cannot use getUpdates while a webhook is configured.
    await bot.delete_webhook(drop_pending_updates=False)
    await clear_command_menu(bot)
    print("GLITCH BOT STARTED IN LOCAL POLLING MODE")
    task = asyncio.create_task(dream_loop())
    try:
        await dp.start_polling(bot)
    finally:
        task.cancel()
        with suppress(asyncio.CancelledError):
            await task


def run_render_webhook():
    """Run aiohttp + aiogram webhook on Render's required public port."""
    dp.startup.register(on_render_startup)

    app = web.Application()
    app.cleanup_ctx.append(dream_lifecycle)
    app.router.add_get("/", healthcheck)
    app.router.add_get("/health", healthcheck)
    app.router.add_post("/memory-tick", memory_tick)

    SimpleRequestHandler(
        dispatcher=dp,
        bot=bot,
        handle_in_background=True,
    ).register(app, path=WEBHOOK_PATH)

    setup_application(app, dp, bot=bot)

    port = int(os.getenv("PORT", "10000"))
    print(f"STARTING RENDER WEB SERVER ON 0.0.0.0:{port}")
    web.run_app(app, host="0.0.0.0", port=port)


if __name__ == "__main__":
    if bot is None:
        raise RuntimeError("BOT_TOKEN is required to run the Telegram bot")
    logging.basicConfig(level=logging.INFO)
    if os.getenv("RENDER") == "true" or os.getenv("RENDER_EXTERNAL_URL"):
        run_render_webhook()
    else:
        asyncio.run(run_local_polling())
