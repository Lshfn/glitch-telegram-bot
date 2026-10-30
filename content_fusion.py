"""Content-derived mathematics shared by images, documents, audio and video.

Signals are bounded projections of decoded content. Randomness chooses the
recipe; the actual upload and its donors supply the fields, phases and kernels.
"""
import random
from io import BytesIO

import numpy as np
from PIL import Image, ImageOps

from artifact_memory import Artifact, extract_artifact

TRACE_SIZE = 2048
IMAGE_METHODS = ("spectral crossover", "content displacement", "reaction diffusion",
                 "bitplane weave", "modular lattice")
AUDIO_METHODS = ("spectral vocoder", "content convolution", "granular permutation",
                 "phase modulation", "wavefold", "sample bitplanes")


def resample(values, size):
    values = np.nan_to_num(np.asarray(values, dtype=np.float32).reshape(-1),
                           nan=0.0, posinf=0.0, neginf=0.0)
    if not len(values):
        return np.zeros(size, dtype=np.float32)
    return np.interp(np.linspace(0, len(values) - 1, size),
                     np.arange(len(values)), values).astype(np.float32)


def unit(values):
    values = np.nan_to_num(np.asarray(values, dtype=np.float32),
                           nan=0.0, posinf=0.0, neginf=0.0)
    peak = float(np.max(np.abs(values))) if values.size else 0
    return values / max(1e-6, peak)


def project(values):
    """Pool across the entire decoded signal, keeping at most 4 KB of int16."""
    values = np.asarray(values, dtype=np.float32).reshape(-1)
    if len(values) > TRACE_SIZE:
        edges = np.linspace(0, len(values), TRACE_SIZE + 1, dtype=np.int64)
        # Signed RMS retains energy that averaging oscillatory audio would erase.
        pooled = np.add.reduceat(values * values, edges[:-1]) / np.diff(edges)
        means = np.add.reduceat(values, edges[:-1])
        values = np.sqrt(pooled) * np.where(means < 0, -1, 1)
    else:
        values = resample(values, min(TRACE_SIZE, max(1, len(values))))
    return (unit(values) * 32767).astype("<i2").tobytes()


def signal_of(artifact):
    if artifact.signal:
        bounded = artifact.signal[:4096]
        return np.frombuffer(bounded[:len(bounded) // 2 * 2], dtype="<i2").astype(np.float32) / 32767
    if artifact.image:
        with Image.open(BytesIO(artifact.image)) as image:
            return np.asarray(image.convert("L").resize((64, 32)),
                              dtype=np.float32).ravel() / 127.5 - 1
    if artifact.text:
        return unit([ord(c) for c in " ".join(artifact.text)])
    return np.frombuffer(artifact.sample, dtype=np.uint8).astype(np.float32) / 127.5 - 1


def trace(data=b"", text="", image=None, waveform=None):
    base = extract_artifact(data, text)
    signals = []
    thumbnail = b""
    if image is not None:
        small = image.convert("RGB").copy()
        small.thumbnail((320, 320))
        buf = BytesIO()
        small.save(buf, "JPEG", quality=70)
        thumbnail = buf.getvalue()
        signals.append(project(np.asarray(small, dtype=np.float32) / 127.5 - 1))
    if text:
        signals.append(project([ord(c) for c in text[:131072]]))
    if waveform is not None:
        signals.append(project(waveform))
    if data:
        signals.append(project(np.frombuffer(data, dtype=np.uint8).astype(np.float32) / 127.5 - 1))
    # Different modalities occupy different parts of one bounded signal.
    joined = np.concatenate([np.frombuffer(s, dtype="<i2").astype(np.float32)
                             for s in signals]) if signals else []
    return Artifact(thumbnail, base.text, base.sample, project(joined))


def merge_traces(original, rendered):
    """Carry both original and transformed material, within the existing bounds."""
    image = rendered.image or original.image
    if original.image and rendered.image:
        with Image.open(BytesIO(original.image)) as a, Image.open(BytesIO(rendered.image)) as b:
            tile = ImageOps.fit(a.convert("RGB"), (160, 160))
            other = ImageOps.fit(b.convert("RGB"), (160, 160))
            sheet = Image.new("RGB", (320, 160))
            sheet.paste(tile, (0, 0))
            sheet.paste(other, (160, 0))
            buf = BytesIO()
            sheet.save(buf, "JPEG", quality=70)
            image = buf.getvalue()
    words = tuple(dict.fromkeys(original.text[:3] + rendered.text[:2]))[:5]
    payload = original.sample[:2048] + rendered.sample[:2048]
    return Artifact(image, words, payload,
                    project(np.concatenate((signal_of(original), signal_of(rendered)))))


def donor_signal(source, artifacts, size):
    sources = [source] + random.sample(list(artifacts), min(4, len(artifacts)))
    values = [signal_of(a) for a in sources]
    values = [v for v in values if len(v)]
    if not values:
        return np.zeros(size, dtype=np.float32)
    mixed = np.zeros(size, dtype=np.float32)
    for i, value in enumerate(values):
        # Keep the contribution of every selected donor, including the original.
        mixed += np.roll(resample(value, size), i * max(1, size // len(values))) / (1 + i * .35)
    return unit(mixed)


def fuse_image(image, source, artifacts, intensity, methods=None):
    level = min(12, max(1, int(intensity)))
    strength = .18 + level * .065
    chosen = list(methods) if methods is not None else random.sample(
        IMAGE_METHODS, random.randint(1, 1 + level // 4))
    working = image.convert("RGB").copy()
    working.thumbnail((768, 768))
    array = np.asarray(working, dtype=np.float32) / 255
    h, w = array.shape[:2]
    field = donor_signal(source, artifacts, h * w).reshape(h, w)
    yy, xx = np.indices((h, w))
    for method in chosen:
        if method == "spectral crossover":
            donor = np.fft.rfft2(field)
            for c in range(3):
                current = np.fft.rfft2(array[:, :, c])
                # Original DC preserves a trace of the source's brightness.
                magnitude = np.abs(donor) * (np.mean(np.abs(current)) /
                                               max(1e-6, np.mean(np.abs(donor))))
                magnitude = (1 - strength * .65) * np.abs(current) + strength * .65 * magnitude
                phase = np.angle(current) + strength * .6 * np.angle(donor)
                crossed = magnitude * np.exp(1j * phase)
                crossed[0, 0] = current[0, 0]
                array[:, :, c] = np.fft.irfft2(crossed, s=(h, w)).real
        elif method == "content displacement":
            dx = (field * w * .22 * strength).astype(np.int32)
            dy = (np.roll(field, max(1, h // 3), axis=0) * h * .15 * strength).astype(np.int32)
            array = array[(yy + dy) % h, (xx + dx) % w]
        elif method == "reaction diffusion":
            # Gray-Scott chemicals start from the image and the donor field.
            sw, sh = min(160, w), min(160, h)
            luminance = Image.fromarray((np.clip(array.mean(2), 0, 1) * 255).astype("uint8"))
            v = np.asarray(luminance.resize((sw, sh)), dtype=np.float32) / 255 * .5
            u = 1 - np.abs(resample(field, sh * sw).reshape(sh, sw)) * .4
            def lap(a):
                return sum(np.roll(a, d, axis=ax) for ax in (0, 1) for d in (-1, 1)) - 4 * a
            feed = .025 + float(np.mean(np.abs(field))) * .025
            for _ in range(12 + level * 3):
                uvv = u * v * v
                u, v = (np.clip(u + .16 * lap(u) - uvv + feed * (1 - u), 0, 1),
                        np.clip(v + .08 * lap(v) + uvv - (.055 + feed) * v, 0, 1))
            mask_image = Image.fromarray((unit(v) * 255).astype("uint8"))
            mask = np.asarray(mask_image.resize((w, h)), dtype=np.float32) / 255
            array = array * (1 - strength * .45) + np.stack(
                (mask, np.roll(mask, w // 8, 1), 1 - mask), axis=2) * strength * .45
        elif method == "bitplane weave":
            pixels = (np.clip(array, 0, 1) * 255).astype("uint8")
            bits = min(7, 1 + level // 2)
            mask = (1 << bits) - 1
            woven = ((field + 1) * 127.5).astype("uint8")
            array = ((pixels & (255 ^ mask)) | (woven[:, :, None] & mask)).astype(np.float32) / 255
        elif method == "modular lattice":
            k = 1 + int(np.mean(np.abs(field)) * level * 3)
            for _ in range(1 + level // 5):
                array = array[(xx + (k + 1) * yy) % h, (xx + k * yy) % w]
        else:
            raise ValueError("Unknown image fusion method")
        array = np.clip(np.nan_to_num(array), 0, 1)
    result = Image.fromarray((array * 255).astype("uint8"))
    return result.resize(image.size, Image.Resampling.NEAREST), chosen


def fuse_audio(samples, source, artifacts, intensity, methods=None):
    level = min(12, max(1, int(intensity)))
    strength = .16 + level * .06
    out = np.nan_to_num(np.asarray(samples, dtype=np.float32), nan=0, posinf=0, neginf=0).copy()
    if out.ndim == 1:
        out = out[:, None]
    n = len(out)
    if not n:
        return out, []
    donor = donor_signal(source, artifacts, n)
    chosen = list(methods) if methods is not None else random.sample(
        AUDIO_METHODS, random.randint(2, min(5, 2 + level // 3)))
    for method in chosen:
        if method == "spectral vocoder":
            block, hop = 1024, 512
            window = np.hanning(block).astype(np.float32)
            crossed = np.zeros((n + 2 * block, out.shape[1]), dtype=np.float32)
            weights = np.zeros(n + 2 * block, dtype=np.float32)
            padded = np.pad(out, ((hop, 2 * block), (0, 0)))
            padded_donor = np.pad(donor, (hop, 2 * block))
            # Overlap-add windows avoid clicks at transform block boundaries.
            for start in range(0, n + hop, hop):
                a = np.fft.rfft(padded[start:start + block] * window[:, None], axis=0)
                b = np.fft.rfft(padded_donor[start:start + block] * window)[:, None]
                mag = np.abs(b) * np.mean(np.abs(a), axis=0) / max(1e-6, np.mean(np.abs(b)))
                mag = (1 - strength * .6) * np.abs(a) + strength * .6 * mag
                phase = np.angle(a) + strength * .45 * np.angle(b)
                segment = np.fft.irfft(mag * np.exp(1j * phase), n=block, axis=0).real
                crossed[start:start + block] += segment * window[:, None]
                weights[start:start + block] += window * window
            out = crossed[hop:hop + n] / np.maximum(weights[hop:hop + n, None], 1e-5)
        elif method == "content convolution":
            kernel = resample(donor, min(256, max(2, n))) * np.exp(-np.linspace(0, 6, min(256, max(2, n))))
            kernel /= max(1, np.sum(np.abs(kernel)))
            kernel[0] += 1
            for c in range(out.shape[1]):
                convolved = np.convolve(out[:, c], kernel, mode="full")[:n]
                out[:, c] = (1 - strength) * out[:, c] + strength * convolved
        elif method == "granular permutation":
            grain = max(64, 2048 - level * 120)
            count = n // grain
            if count > 1:
                order = np.argsort(resample(donor, count), kind="stable")
                grains = out[:count * grain].reshape(count, grain, -1).copy()[order]
                # Short envelopes soften boundaries while keeping the time cuts.
                fade = min(32, grain // 8)
                grains[:, :fade] *= np.linspace(.1, 1, fade)[None, :, None]
                grains[:, -fade:] *= np.linspace(1, .1, fade)[None, :, None]
                out[:count * grain] = grains.reshape(count * grain, -1)
        elif method == "phase modulation":
            phase = np.cumsum(.01 + np.abs(donor) * (.02 + strength * .3), dtype=np.float64)
            out *= ((1 - strength * .7) + strength * .7 * np.cos(phase))[:, None]
        elif method == "wavefold":
            out = np.sin(out * (1 + level * .6) + donor[:, None] * strength * .2)
        elif method == "sample bitplanes":
            quantized = (np.clip(out, -1, 1) * 32767).astype("int16")
            mask = (1 << (2 + level // 2)) - 1
            raw = ((donor + 1) * 16383).astype("int16") & mask
            out = (quantized ^ raw[:, None]).astype(np.float32) / 32767
        else:
            raise ValueError("Unknown audio fusion method")
        out = np.nan_to_num(out, nan=0, posinf=0, neginf=0)
        peak = np.max(np.abs(out))
        if peak > .92:
            out *= .92 / peak
    return out.astype(np.float32), chosen


def fuse_text(text, source, artifacts, intensity):
    """Permute actual words through a content-driven logistic recurrence."""
    words = text.split()
    if len(words) < 2:
        return text, []
    level = min(12, max(1, int(intensity)))
    field = donor_signal(source, artifacts, min(4096, len(words)))
    window = min(len(words), 3 + level * 2)
    result = list(words)
    # Reorder within local windows; avoid expanding a document without bounds.
    x = .1 + (float(np.mean(np.abs(field))) % .8)
    for start in range(0, len(words), window):
        block = result[start:start + window]
        keys = []
        for i in range(len(block)):
            x = np.clip((3.65 + .34 * abs(float(field[(start + i) % len(field)]))) * x * (1 - x), .001, .999)
            keys.append(x)
        result[start:start + window] = [block[i] for i in np.argsort(keys, kind="stable")]
    # Preserve the source's separators/newlines, including subtitle timing lines.
    import re
    iterator = iter(result)
    return re.sub(r"\S+", lambda _: next(iterator), text), ["logistic word weave"]


def fuse_bytes(data, source, artifacts, intensity):
    """Algebra on payload bytes; unknown containers may still become unreadable."""
    if len(data) < 64:
        return data, []
    level = min(12, max(1, int(intensity)))
    out = bytearray(data)
    start, end = max(16, len(out) // 6), min(len(out) - 8, len(out) * 5 // 6)
    field = donor_signal(source, artifacts, 4096)
    donor = np.clip((field + 1) * 127.5, 0, 255).astype("uint8")
    method = random.choice(("finite field bytes", "byte convolution", "modular byte weave"))
    for _ in range(1 + level):
        span = min(end - start, 32 + level * 128)
        position = random.randint(start, end - span)
        a = np.frombuffer(bytes(out[position:position + span]), dtype="uint8")
        b = np.resize(np.roll(donor, random.randrange(len(donor))), span)
        if method == "finite field bytes":
            # Rijndael's GF(2^8), with a content-derived nonzero multiplier.
            aa, bb, product = a.copy(), b | 1, np.zeros(span, dtype="uint8")
            for _ in range(8):
                product ^= np.where(bb & 1, aa, 0).astype("uint8")
                high = aa & 128
                aa = (aa << 1) ^ np.where(high, 0x1b, 0).astype("uint8")
                bb >>= 1
            fused = product
        elif method == "byte convolution":
            kernel = b[:min(9, span)].astype(np.int64)
            fused = (np.convolve(a.astype(np.int64), kernel, mode="full")[:span] % 256).astype("uint8")
        else:
            order = np.argsort(b, kind="stable")
            fused = a[order] ^ b
        out[position:position + span] = fused.tobytes()
    return bytes(out), [method]
