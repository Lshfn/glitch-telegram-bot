"""Real translations, optional local OCR, and replacement inside text regions."""
import asyncio
import html
import os
import random
import re
import shutil
import time
from dataclasses import dataclass

import aiohttp
from PIL import Image, ImageDraw, ImageFont, ImageStat

TEXT_EXTENSIONS = (".txt", ".md", ".log", ".rst", ".srt", ".vtt")


def decode_text(data):
    if data.startswith((b"\xff\xfe", b"\xfe\xff")):
        return data.decode("utf-16"), "utf-16"
    if data.startswith(b"\xef\xbb\xbf"):
        return data.decode("utf-8-sig"), "utf-8-sig"
    try:
        return data.decode("utf-8"), "utf-8"
    except UnicodeDecodeError:
        return data.decode("cp1251"), "cp1251"


def font(size):
    paths = [os.getenv("GLITCH_FONT", ""),
             "/usr/share/fonts/truetype/noto/NotoSans-Regular.ttf",
             "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"]
    for path in paths:
        if path and os.path.isfile(path):
            return ImageFont.truetype(path, size=max(1, size))
    return ImageFont.load_default(size=max(1, size))


@dataclass(frozen=True)
class TextRegion:
    box: tuple[int, int, int, int]
    text: str


def read_regions(image):
    if not shutil.which("tesseract"):
        from local_ocr import read_data
        data = read_data(image)
    else:
        import pytesseract
        available = set(pytesseract.get_languages())
        requested = os.getenv("OCR_LANGUAGES", "eng+rus").split("+")
        languages = "+".join(lang for lang in requested if lang in available)
        if not languages:
            raise RuntimeError("Requested OCR language packs are not installed")
        data = pytesseract.image_to_data(image, lang=languages, config="--psm 11",
                                        output_type=pytesseract.Output.DICT, timeout=8)
    lines = {}
    for i, text in enumerate(data["text"]):
        text = text.strip()
        if not text or float(data["conf"][i]) < 45:
            continue
        key = (data["page_num"][i], data["block_num"][i], data["par_num"][i], data["line_num"][i])
        x, y, w, h = (int(data[k][i]) for k in ("left", "top", "width", "height"))
        lines.setdefault(key, []).append((text, x, y, x + w, y + h))
    regions = []
    for words in lines.values():
        text = " ".join(w[0] for w in words)
        if any(c.isalpha() for c in text) and len(text) <= 240:
            regions.append(TextRegion((min(w[1] for w in words), min(w[2] for w in words),
                                       max(w[3] for w in words), max(w[4] for w in words)), text))
    return regions[:80]


def paint_translation(image, region, translated):
    """Replace a detected line; shrink to fit rather than drawing outside its box."""
    x0, y0, x1, y1 = region.box
    x0, y0 = max(0, x0 - 2), max(0, y0 - 2)
    x1, y1 = min(image.width, x1 + 2), min(image.height, y1 + 2)
    width, height = x1 - x0, y1 - y0
    if width < 1 or height < 1:
        return image
    # Approximate local background from the boundary, not the original glyphs.
    border = image.crop((x0, y0, x1, min(y1, y0 + 1)))
    background = tuple(int(c) for c in ImageStat.Stat(border).median[:3])
    foreground = (15, 15, 15) if sum(background) > 380 else (245, 245, 245)
    tile = Image.new("RGB", (width, height), background)
    draw = ImageDraw.Draw(tile)
    size = min(64, max(1, height))
    while size > 1:
        face = font(size)
        box = draw.textbbox((0, 0), translated, font=face)
        if box[2] - box[0] <= width and box[3] - box[1] <= height:
            break
        size -= 1
    face = font(size)
    box = draw.textbbox((0, 0), translated, font=face)
    draw.text((-box[0], -box[1]), translated, font=face, fill=foreground)
    image.paste(tile, (x0, y0))
    return image


class Translator:
    def __init__(self, url="", api_key="", targets=None, provider="libretranslate"):
        if provider not in ("libretranslate", "mymemory"):
            raise ValueError("Unsupported TRANSLATE_PROVIDER")
        self.provider = provider
        self.url = url.rstrip("/")
        self.api_key = api_key
        self.targets = targets or ["en", "ru", "de", "fr", "es", "it", "pt", "el", "uk", "pl", "tr"]
        self.retry_at = 0

    @property
    def configured(self):
        return self.provider == "mymemory" or bool(self.url)

    async def request(self, session, text, target):
        timeout = aiohttp.ClientTimeout(total=12 if self.provider == "mymemory" else 5)
        if self.provider == "mymemory":
            if time.monotonic() < self.retry_at:
                raise ValueError("Translation quota temporarily unavailable")
            # MyMemory accepts at most 500 UTF-8 bytes, rather than characters.
            query = text.encode("utf-8")[:500].decode("utf-8", errors="ignore")
            async with session.get("https://api.mymemory.translated.net/get",
                                   params={"q": query, "langpair": f"autodetect|{target}"},
                                   timeout=timeout, allow_redirects=False) as response:
                response.raise_for_status()
                result = await response.json()
            if not isinstance(result, dict):
                raise ValueError("Translation service returned an invalid response")
            if result.get("quotaFinished"):
                self.retry_at = time.monotonic() + 3600
                raise ValueError("Translation quota reached")
            if str(result.get("responseStatus")) != "200":
                raise ValueError("Translation service could not translate this phrase")
            data = result.get("responseData")
            if not isinstance(data, dict):
                raise ValueError("Translation service returned an invalid response")
            value = data.get("translatedText")
            source = data.get("detectedLanguage")
            if isinstance(value, str):
                value = html.unescape(value)
        else:
            payload = {"q": text, "source": "auto", "target": target, "format": "text"}
            if self.api_key:
                payload["api_key"] = self.api_key
            async with session.post(self.url + "/translate", json=payload,
                                    timeout=timeout, allow_redirects=False) as response:
                response.raise_for_status()
                result = await response.json()
            if not isinstance(result, dict):
                raise ValueError("Translation service returned an invalid response")
            value = result.get("translatedText")
            detected = result.get("detectedLanguage")
            source = detected.get("language") if isinstance(detected, dict) else None
        if not isinstance(value, str) or not value.strip():
            raise ValueError("Translation service returned no text")
        return value, source

    async def translate(self, session, text):
        choices = list(dict.fromkeys(self.targets))
        random.shuffle(choices)
        for target in choices[:2]:
            value, source = await self.request(session, text[:240], target)
            if value.strip() != text.strip() and source != target:
                return value[:600], target
        return text, None

    async def replace_spans(self, text, session):
        # Never translate whitespace, timestamps or an entire large file at once.
        spans = [m for m in re.finditer(r"[^\n.!?]{3,180}(?:[.!?]|(?=\n|$))", text[:32000])
                 if any(c.isalpha() for c in m.group())]
        selected = sorted(random.sample(spans, min(len(spans), random.randint(2, 5))),
                          key=lambda m: m.start(), reverse=True)
        targets, failures = [], 0
        deadline = time.monotonic() + 22
        for index, match in enumerate(selected):
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                failures += len(selected) - index
                break
            try:
                async with asyncio.timeout(remaining):
                    value, target = await self.translate(session, match.group())
            except (aiohttp.ClientError, asyncio.TimeoutError, ValueError):
                failures += 1
                continue
            if target:
                text = text[:match.start()] + value + text[match.end():]
                targets.append(target)
        return text, targets, failures

    async def replace_regions(self, image, regions, session):
        targets, failures = [], 0
        selected = random.sample(regions, min(len(regions), random.randint(2, 5)))
        deadline = time.monotonic() + 22
        for index, region in enumerate(selected):
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                failures += len(selected) - index
                break
            try:
                async with asyncio.timeout(remaining):
                    value, target = await self.translate(session, region.text)
            except (aiohttp.ClientError, asyncio.TimeoutError, ValueError):
                failures += 1
                continue
            if target:
                await asyncio.to_thread(paint_translation, image, region, value)
                targets.append(target)
        return image, targets, failures
