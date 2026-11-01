"""Image-guided regions and multiscale blending without geometric cutouts."""
import heapq
import random

import numpy as np
from PIL import Image, ImageFilter


def _noise(size):
    rng = np.random.default_rng(random.getrandbits(32))
    field = rng.random((7, 7)).astype(np.float32)
    return np.asarray(Image.fromarray(field).resize(size, Image.Resampling.BICUBIC))


def _features(image, guide=None):
    pixels = np.asarray(image.convert("RGB"), dtype=np.float32) / 255
    grey = pixels.mean(axis=2)
    blur = np.asarray(image.convert("L").filter(ImageFilter.GaussianBlur(2)), dtype=np.float32) / 255
    edges = np.abs(grey - blur)
    edges /= max(.02, float(edges.max()))
    if guide is not None:
        other = np.asarray(guide.convert("RGB").resize(image.size), dtype=np.float32) / 255
        pixels = np.concatenate((pixels * .65, other * .35), axis=2)
    return pixels, grey, edges


def _fade(mask):
    h, w = mask.shape
    if min(w, h) < 4:
        return mask
    x = np.minimum(np.arange(w), np.arange(w)[::-1]) / max(1, w * .07)
    y = np.minimum(np.arange(h), np.arange(h)[::-1]) / max(1, h * .07)
    return mask * np.minimum(1, x)[None, :] * np.minimum(1, y)[:, None]


def content_mask(image, guide=None, coverage=.35):
    """Grow through related colors and texture, stopping at image boundaries.

    The search is bounded to a 48px grid. Regions follow the pictures rather
    than an ellipse, rectangle, or brush stamp. Flat inputs use an irregular
    field to break ties, without imposing a geometric aperture.
    """
    small = image.convert("RGB").copy()
    small.thumbnail((48, 48))
    w, h = small.size
    pixels, grey, edges = _features(small, guide)
    noise = _noise(small.size)
    sy, sx = divmod(random.randrange(w * h), w)
    similarity = np.mean(np.abs(pixels - pixels[sy, sx]), axis=2)
    cost = similarity * 2 + edges * .7 + noise * .3
    chosen, queued = np.zeros((h, w), dtype=bool), np.zeros((h, w), dtype=bool)
    queue = [(0., sy, sx)]
    queued[sy, sx] = True
    limit = max(1, min(w * h, round(w * h * coverage)))
    for _ in range(limit):
        if not queue:
            break
        priority, y, x = heapq.heappop(queue)
        chosen[y, x] = True
        for yy, xx in ((y - 1, x), (y + 1, x), (y, x - 1), (y, x + 1)):
            if 0 <= xx < w and 0 <= yy < h and not queued[yy, xx]:
                queued[yy, xx] = True
                heapq.heappush(queue, (float(cost[yy, xx]) + priority * .03, yy, xx))
    mask = Image.fromarray((chosen * 255).astype("uint8"))
    mask = mask.filter(ImageFilter.GaussianBlur(.8)).resize(image.size, Image.Resampling.BICUBIC)
    return Image.fromarray(np.clip(_fade(np.asarray(mask, dtype=np.float32)), 0, 255).astype("uint8"))


def detail_mask(image, guide=None, coverage=.6):
    """Fast texture-following opacity for individual agent moves."""
    small = image.convert("RGB").copy()
    small.thumbnail((96, 96))
    _, grey, edges = _features(small, guide)
    score = grey * .35 + edges * .65 + _noise(small.size) * .18
    threshold = np.quantile(score, 1 - coverage)
    mask = np.clip((score - threshold) / max(.04, float(np.std(score))) + .45, 0, 1)
    mask = _fade(mask)
    return Image.fromarray((mask * 255).astype("uint8")).resize(image.size, Image.Resampling.BILINEAR)


def multiscale_blend(base, donor, mask, opacity=.8):
    """Blend picture detail at several scales so the boundary is not a cut edge."""
    original, original_mask = base.convert("RGB"), mask.copy()
    size = base.size
    base = base.convert("RGB").copy()
    base.thumbnail((768, 768))
    donor = donor.convert("RGB").resize(base.size, Image.Resampling.LANCZOS)
    mask = mask.resize(base.size, Image.Resampling.BILINEAR)
    a, b, masks = [base], [donor], [mask]
    for _ in range(4):
        if min(a[-1].size) < 4:
            break
        smaller = (max(1, a[-1].width // 2), max(1, a[-1].height // 2))
        for pyramid in (a, b, masks):
            pyramid.append(pyramid[-1].filter(ImageFilter.GaussianBlur(1)).resize(smaller, Image.Resampling.BILINEAR))
    result = None
    for i in range(len(a) - 1, -1, -1):
        aa, bb = np.asarray(a[i], dtype=np.float32), np.asarray(b[i], dtype=np.float32)
        alpha = np.asarray(masks[i], dtype=np.float32)[:, :, None] / 255 * opacity
        if result is None:
            result = aa * (1 - alpha) + bb * alpha
        else:
            up_a = np.asarray(a[i + 1].resize(a[i].size, Image.Resampling.BILINEAR), dtype=np.float32)
            up_b = np.asarray(b[i + 1].resize(a[i].size, Image.Resampling.BILINEAR), dtype=np.float32)
            up = np.asarray(Image.fromarray(np.clip(result, 0, 255).astype("uint8")).resize(a[i].size, Image.Resampling.BILINEAR), dtype=np.float32)
            result = up + (aa - up_a) * (1 - alpha) + (bb - up_b) * alpha
    result = Image.fromarray(np.clip(result, 0, 255).astype("uint8")).resize(size, Image.Resampling.LANCZOS)
    support = original_mask.point(lambda p: 255 if p > 1 else 0)
    return Image.composite(result, original, support)
