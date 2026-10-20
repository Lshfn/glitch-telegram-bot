"""Cross-upload effects: crops, scan fragments, text echoes and byte grafts."""
import random
from io import BytesIO

from PIL import Image, ImageChops, ImageDraw, ImageOps

from language_glitch import font


def inherit_image(image, artifacts, strength):
    if not artifacts:
        return image, []
    out = image.copy()
    sources = []
    for artifact in random.sample(artifacts, min(len(artifacts), 4)):
        if artifact.image:
            sources.append(Image.open(BytesIO(artifact.image)).convert("RGB"))
        elif artifact.sample:
            raw = artifact.sample[:768].ljust(768, b"\x00")
            sources.append(Image.frombytes("RGB", (32, 8), raw))
    labels = []
    if sources:
        style = random.choice(("splice", "ghost", "difference", "mosaic", "channel"))
        for _ in range(random.randint(2, 4 + int(strength * 8))):
            donor = random.choice(sources)
            w = random.randint(1, max(1, donor.width))
            h = random.randint(1, max(1, donor.height))
            x, y = random.randrange(donor.width - w + 1), random.randrange(donor.height - h + 1)
            crop = donor.crop((x, y, x + w, y + h))
            tw = random.randint(1, max(1, out.width // (2 if style == "mosaic" else 3)))
            th = random.randint(1, max(1, out.height // 3))
            if style == "splice":
                tw, th = out.width, random.randint(1, max(1, out.height // 12))
            crop = crop.resize((tw, th), Image.Resampling.NEAREST)
            x, y = random.randrange(out.width - tw + 1), random.randrange(out.height - th + 1)
            base = out.crop((x, y, x + tw, y + th))
            if style == "ghost":
                crop = Image.blend(base, crop, random.uniform(.18, .65))
            elif style == "difference":
                crop = ImageChops.difference(base, crop)
            elif style == "channel":
                channels = list(base.split())
                channels[random.randrange(3)] = random.choice(crop.split())
                crop = Image.merge("RGB", channels)
            out.paste(crop, (x, y))
        labels.append("inherited " + style)
    snippets = [t for a in artifacts for t in a.text]
    if snippets and (not sources or random.random() < .65):
        draw = ImageDraw.Draw(out)
        for _ in range(random.randint(1, 3)):
            text = random.choice(snippets)[:100]
            size = max(6, min(40, out.width // 22))
            draw.text((random.randrange(out.width), random.randrange(out.height)), text,
                      font=font(size), fill=random.choice(("white", "black", "magenta", "cyan")))
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
