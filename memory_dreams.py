"""New playable files made only from one private history's bounded traces."""
import random
import textwrap
import wave
from io import BytesIO

import numpy as np
from PIL import Image, ImageDraw

from content_fusion import donor_signal, fuse_audio, fuse_image, fuse_text, resample, signal_of, unit
from language_glitch import font
from memory_effects import inherit_image


def mix_image(artifacts, level):
    pictures = []
    for artifact in artifacts:
        data = artifact.original_image or artifact.image
        if data:
            try:
                with Image.open(BytesIO(data)) as source:
                    if source.width * source.height <= 1_000_000:
                        pictures.append((artifact, source.convert("RGB")))
            except (OSError, ValueError):
                continue
    if not pictures:
        # A visual field can also be grown from text and sound traces.
        field = donor_signal(artifacts[0], artifacts[1:], 128 * 128 * 3)
        image = Image.fromarray(np.clip((field.reshape(128, 128, 3) + 1) * 127.5, 0, 255).astype("uint8"))
        source = artifacts[0]
    else:
        source, image = random.choice(pictures)
    scale = 640 / max(image.size)
    image = image.resize((max(1, round(image.width * scale)), max(1, round(image.height * scale))), Image.Resampling.LANCZOS)
    donors = [a for a in artifacts if a is not source]
    image, _ = inherit_image(image, donors, .3 + level * .035)
    image, _ = fuse_image(image, source, donors, level, methods=["content displacement"])
    return image


def mix_text(artifacts, level):
    fragments = [random.choice(a.text) for a in artifacts if a.text]
    random.shuffle(fragments)
    text = "\n\n".join(fragments)[:6000]
    return fuse_text(text, artifacts[0], artifacts[1:], level)[0]


def make_dream(artifacts, kind=None):
    """Two or more donor entries; no original file names or generated feedback."""
    artifacts = list(artifacts)[:16]
    if len(artifacts) < 2:
        raise ValueError("At least two remembered files are needed")
    kinds, weights = ["png", "pdf", "wav"], [6, 1, 2]
    if any(a.text for a in artifacts):
        kinds.append("txt")
        weights.append(3)
    kind = kind or random.choices(kinds, weights=weights)[0]
    if kind not in kinds:
        raise ValueError("No suitable memory for this format")
    level = random.choices(range(1, 13), weights=(2, 3, 4, 5, 5, 4, 3, 2, 2, 1, 1, 1))[0]
    if kind == "txt":
        return mix_text(artifacts, level).encode("utf-8"), "memory.txt"
    output = BytesIO()
    if kind in ("png", "pdf"):
        image = mix_image(artifacts, level)
        if kind == "png":
            image.save(output, "PNG")
        else:
            page = Image.new("RGB", (800, 1100), "#f4f0ea")
            image.thumbnail((720, 650))
            page.paste(image, ((800 - image.width) // 2, 40))
            lines = textwrap.wrap(mix_text(artifacts, level), width=65)[:14]
            ImageDraw.Draw(page).multiline_text((40, 740), "\n".join(lines), fill="#26202b", font=font(18), spacing=5)
            page.save(output, "PDF", resolution=100, quality=80)
    else:
        rate = 22050
        size = rate * random.randint(6, 12)
        # Signal traces are projections of file content, not recordings of old audio.
        value = signal_of(artifacts[0])
        samples = resample(value, size)
        mixed = donor_signal(artifacts[0], artifacts[1:], size)
        samples = np.column_stack((samples * .4 + mixed * .6, np.roll(mixed, rate // 7)))
        samples, _ = fuse_audio(samples, artifacts[0], artifacts[1:], level,
                                methods=random.sample(["phase modulation", "wavefold", "content convolution"], 2))
        samples = unit(samples) * .65
        fade = min(rate // 20, len(samples) // 2)
        samples[:fade] *= np.linspace(0, 1, fade)[:, None]
        samples[-fade:] *= np.linspace(1, 0, fade)[:, None]
        with wave.open(output, "wb") as audio:
            audio.setparams((2, 2, rate, 0, "NONE", "not compressed"))
            audio.writeframes((samples * 32767).astype("<i2").tobytes())
    return output.getvalue(), "memory." + kind
