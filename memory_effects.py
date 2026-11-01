"""Cross-upload effects: crops, scan fragments, text echoes and byte grafts."""
import random
from io import BytesIO

from PIL import Image, ImageChops, ImageDraw, ImageOps, ImageFilter
import numpy as np

from language_glitch import font
from image_surfaces import content_mask, detail_mask, multiscale_blend

MEMORY_SURFACES = ("palimpsest", "texture graft", "ghost field", "channel haunt", "recursive field")
AUTOMATIC_MEMORY_SURFACES = MEMORY_SURFACES[:-1]


def visual_donors(artifacts, prefer_original=True):
    """Prefer intact thumbnails; old databases fall back to their existing traces."""
    sources = []
    for artifact in artifacts:
        data = (artifact.original_image or artifact.image) if prefer_original else artifact.image
        if data:
            with Image.open(BytesIO(data)) as donor:
                sources.append(donor.convert("RGB"))
    return sources


def inherit_image(image, artifacts, strength, style=None):
    if not artifacts:
        return image, []
    out = image.copy()
    sources = visual_donors(random.sample(list(artifacts), min(len(artifacts), 6)))
    if not sources:
        for artifact in artifacts:
            if artifact.sample:
                raw = artifact.sample[:768].ljust(768, b"\x00")
                sources.append(Image.frombytes("RGB", (16, 16), raw))
                break
    labels = []
    if sources:
        style = style or random.choices(AUTOMATIC_MEMORY_SURFACES, (4, 3, 2, 1))[0]
        if style in ("recursive", "recursive field"):
            sources = visual_donors(artifacts, prefer_original=False) or sources
        if style in MEMORY_SURFACES:
            donor = ImageOps.fit(random.choice(sources), out.size, Image.Resampling.LANCZOS)
            if style == "texture graft":
                radius = max(1, min(out.size) * .035)
                color = np.asarray(out.filter(ImageFilter.GaussianBlur(radius)), dtype=np.float32)
                donor_color = np.asarray(donor.filter(ImageFilter.GaussianBlur(radius)), dtype=np.float32)
                pixels = np.asarray(donor, dtype=np.float32) + (color - donor_color) * .8
                donor = Image.fromarray(np.clip(pixels, 0, 255).astype("uint8"))
            elif style == "channel haunt":
                channels = list(out.split())
                donor_channels = donor.split()
                changed = [c for c in range(3) if ImageChops.difference(channels[c], donor_channels[c]).getbbox()]
                c = random.choice(changed or list(range(3)))
                channels[c] = donor_channels[c]
                donor = Image.merge("RGB", channels)
            mask = content_mask(out, donor, random.uniform(.22, .52))
            opacity = random.uniform(.35, .6) if style == "ghost field" else random.uniform(.65, .95)
            out = multiscale_blend(out, donor, mask, opacity)
        for _ in range(0 if style in MEMORY_SURFACES else random.randint(1, 2 + int(strength > .65))):
            donor = random.choice(sources)
            # Crop a substantial region, then resize at one scale on both axes.
            fraction = 1.0 if style == "window" else random.uniform(.6, 1)
            w, h = max(1, round(donor.width * fraction)), max(1, round(donor.height * fraction))
            sx, sy = random.randrange(donor.width - w + 1), random.randrange(donor.height - h + 1)
            crop = donor.crop((sx, sy, sx + w, sy + h))
            scale = min(out.width * random.uniform(.2, .65) / w, out.height * .65 / h)
            tw, th = max(1, min(out.width, round(w * scale))), max(1, min(out.height, round(h * scale)))
            if style == "splice":
                # A rare thin cut retains its aspect ratio instead of stretching.
                th = min(th, max(1, out.height // 30))
                crop = crop.crop((0, 0, w, max(1, round(th / max(scale, .001)))))
            crop = crop.resize((tw, th), Image.Resampling.LANCZOS)
            x, y = random.randrange(out.width - tw + 1), random.randrange(out.height - th + 1)
            base = out.crop((x, y, x + tw, y + th))
            mask = Image.new("L", (tw, th), 255)
            if style == "ghost":
                mask = Image.new("L", (tw, th), random.randint(100, 200))
            elif style == "contour":
                mask = crop.convert("L").filter(ImageFilter.FIND_EDGES)
                mask = ImageOps.autocontrast(mask).point(lambda p: 230 if p > 30 else 0)
            elif style == "difference":
                crop = ImageChops.difference(base, crop)
            elif style == "channel":
                channels = list(base.split())
                channels[random.randrange(3)] = random.choice(crop.split())
                crop = Image.merge("RGB", channels)
            elif style == "fragment":
                mask = detail_mask(base, crop, .65)
            out.paste(crop, (x, y), mask)
        labels.append("inherited " + style)
    snippets = [t for a in artifacts for t in a.text]
    if snippets and (not sources or random.random() < .55):
        draw = ImageDraw.Draw(out)
        for _ in range(random.randint(1, 2)):
            text = random.choice(snippets)[:100]
            size = max(6, min(40, out.width // 22))
            face = font(size)
            # Keep the beginning of the remembered phrase inside the canvas.
            x = random.randrange(max(1, out.width // 2))
            y = random.randrange(max(1, out.height - size))
            draw.text((x, y), text, font=face, stroke_width=1, stroke_fill="black",
                      fill=random.choice(("white", "magenta", "cyan")))
        labels.append("inherited text")
    return out, labels


def inherit_bytes(data, artifacts, intensity):
    donors = [a.sample for a in artifacts if a.sample]
    if not donors or len(data) < 64:
        return data, []
    result = bytearray(data)
    start, end = max(16, len(data) // 5), min(len(data) - 8, len(data) * 4 // 5)
    for _ in range(random.randint(1, min(8, 1 + intensity))):
        donor = random.choice(donors)
        size = random.randint(1, min(len(donor), max(1, (end - start) // 4), 512))
        source = random.randrange(len(donor) - size + 1)
        target = random.randint(start, end - size)
        result[target:target + size] = donor[source:source + size]
    return bytes(result), ["inherited bytes"]


def glitch_text(text, artifacts, intensity):
    labels = []
    donors = [t for a in artifacts for t in a.text]
    if donors:
        for _ in range(random.randint(1, min(5, 1 + intensity))):
            # Insert only at a word boundary; output stays valid Unicode.
            positions = [0, len(text)] + [i for i, c in enumerate(text) if c == "\n"]
            pos = random.choice(positions)
            text = text[:pos] + "\n" + random.choice(donors) + "\n" + text[pos:]
        labels.append("inherited text")
    else:
        # Binary/image traces can haunt a text document too.
        raw = [a.sample for a in artifacts if a.sample]
        if raw:
            pos = random.randrange(len(text) + 1)
            text = text[:pos] + "\n" + random.choice(raw)[:32].hex(" ") + "\n" + text[pos:]
            labels.append("inherited hex")
    lines = text.splitlines(keepends=True)
    if lines:
        mode = random.choice(("echo", "reorder", "stutter"))
        if mode == "reorder" and len(lines) > 1:
            a, b = random.sample(range(len(lines)), 2)
            lines[a], lines[b] = lines[b], lines[a]
        else:
            i = random.randrange(len(lines))
            if mode == "stutter":
                words = lines[i].split()
                if words:
                    j = random.randrange(len(words))
                    words[j:j] = [words[j]] * random.randint(1, 3)
                    lines[i] = " ".join(words) + "\n"
            else:
                lines.insert(i, lines[i])
        labels.append("text " + mode)
    return "".join(lines), labels
