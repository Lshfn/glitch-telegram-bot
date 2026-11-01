"""Motion-driven feedback and elementary MPEG-4 I-frame removal."""
import re

import numpy as np
from PIL import Image


def frame_types(data):
    return [("I", "P", "B", "S")[data[m.end()] >> 6]
            for m in re.finditer(b"\x00\x00\x01\xb6", data) if m.end() < len(data)]


def remove_intra_frames(data):
    """Keep the initial I-VOP and P-VOPs, remove later I-VOP payloads.

    Only used on our own MPEG-4 Part 2 stream encoded without B frames.
    Start codes and their payloads are handled as separate units; VOL/GOP
    headers are retained. The decoder result is rebuilt as a valid MP4.
    """
    units = list(re.finditer(b"\x00\x00\x01[\x00-\xff]", data))
    result = bytearray(data[:units[0].start()] if units else data)
    seen_intra, removed = False, 0
    for i, unit in enumerate(units):
        end = units[i + 1].start() if i + 1 < len(units) else len(data)
        chunk = data[unit.start():end]
        if chunk[3] == 0xB6 and len(chunk) > 4 and chunk[4] >> 6 == 0:
            if seen_intra:
                removed += 1
                continue
            seen_intra = True
        result.extend(chunk)
    return bytes(result), removed


def motion_feedback(image, previous, current, feedback, strength, mode="melt"):
    """Estimate block motion from decoded frames and move the stale canvas.

    The local matching is deliberately bounded (96px, 8x8 blocks, 25 candidates).
    Most residual information is withheld until a later temporal recovery.
    """
    previous = previous.resize(image.size, Image.Resampling.BILINEAR)
    current = current.resize(image.size, Image.Resampling.BILINEAR)
    feedback = feedback.resize(image.size, Image.Resampling.BILINEAR)
    a = np.asarray(previous.convert("L").resize((96, 96)), dtype=np.float32)
    b = np.asarray(current.convert("L").resize((96, 96)), dtype=np.float32)
    pad = np.pad(a, 4, mode="edge")
    offsets, errors = [], []
    for dy in (-4, -2, 0, 2, 4):
        for dx in (-4, -2, 0, 2, 4):
            shifted = pad[4 + dy:100 + dy, 4 + dx:100 + dx]
            error = ((shifted - b) ** 2).reshape(8, 12, 8, 12).mean(axis=(1, 3))
            # Prefer a zero vector when several matches are equally plausible.
            errors.append(error + (abs(dx) + abs(dy)) * .15)
            offsets.append((dy, dx))
    choice = np.argmin(np.stack(errors), axis=0)
    vectors = np.asarray(offsets)[choice]
    gain = 1.8 if mode == "bloom" else 1.0
    dx = np.asarray(Image.fromarray(vectors[:, :, 1].astype(np.float32)).resize(image.size, Image.Resampling.NEAREST))
    dy = np.asarray(Image.fromarray(vectors[:, :, 0].astype(np.float32)).resize(image.size, Image.Resampling.NEAREST))
    yy, xx = np.indices((image.height, image.width))
    sx = np.clip(xx + np.rint(dx * image.width / 96 * gain).astype(int), 0, image.width - 1)
    sy = np.clip(yy + np.rint(dy * image.height / 96 * gain).astype(int), 0, image.height - 1)
    stale = np.asarray(feedback, dtype=np.float32)[sy, sx]
    residual = np.asarray(current, dtype=np.float32) - np.asarray(previous, dtype=np.float32)[sy, sx]
    gain = .035 if mode == "bloom" else .24 - min(1, strength) * .16
    moshed = np.clip(stale + residual * gain, 0, 255)
    fresh = np.asarray(image, dtype=np.float32)
    # A little current information keeps motion legible even during long blooms.
    fresh_weight = .025 if mode == "bloom" else .08
    return Image.fromarray((moshed * (1 - fresh_weight) + fresh * fresh_weight).astype("uint8"))
