"""Bounded editing agents, inspired by the local tool interactions in RRR's 0..1.

Agents read the changing canvas, move towards features, and leave short strokes.
No model, network request, or downloaded reference imagery is involved.
"""
import math
import random

import numpy as np
from PIL import Image, ImageChops, ImageDraw, ImageFilter, ImageOps

from memory_effects import visual_donors

TOOLS = ("smudge", "sharpen", "restore", "sampler", "prism", "pixel trail", "contour ink")


def agent_canvas(image, original, artifacts, intensity, rounds=None):
    level = min(12, max(1, intensity))
    canvas = image.convert("RGB").copy()
    canvas.thumbnail((640, 640))
    before = canvas.copy()
    original = original.resize(canvas.size, Image.Resampling.LANCZOS)
    donors = visual_donors(artifacts) or [original]
    # One selected island resists all tools, including the sampler.
    protected = Image.new("L", canvas.size)
    radius = max(1, int(min(canvas.size) * random.uniform(.06, .16)))
    px, py = random.randrange(canvas.width), random.randrange(canvas.height)
    ImageDraw.Draw(protected).ellipse((px - radius, py - radius, px + radius, py + radius), fill=255)
    agents = [{"x": random.randrange(canvas.width), "y": random.randrange(canvas.height),
               "tool": random.choice(TOOLS), "radius": random.uniform(.025, .10)}
              for _ in range(random.randint(2, 4))]
    labels = ["editing agents", "protected island"]
    for _ in range(rounds if rounds is not None else 3 + level // 2):
        random.shuffle(agents)
        # Heatmaps are recomputed from the canvas as it changes.
        grey = np.asarray(canvas.convert("L").resize((48, 48)), dtype=np.float32) / 255
        edges = np.asarray(canvas.filter(ImageFilter.FIND_EDGES).convert("L").resize((48, 48)), dtype=np.float32) / 255
        for agent in agents:
            tool = agent["tool"]
            candidates = []
            for _ in range(8):
                angle, distance = random.uniform(0, math.tau), random.uniform(.02, .18)
                x = min(canvas.width - 1, max(0, round(agent["x"] + math.cos(angle) * canvas.width * distance)))
                y = min(canvas.height - 1, max(0, round(agent["y"] + math.sin(angle) * canvas.height * distance)))
                gx, gy = min(47, x * 48 // canvas.width), min(47, y * 48 // canvas.height)
                score = (1 - edges[gy, gx] if tool == "sharpen" else edges[gy, gx])
                if tool in ("sampler", "restore"):
                    score = 1 - grey[gy, gx]
                candidates.append((float(score) + random.uniform(0, .25), x, y))
            _, x, y = max(candidates)
            r = max(1, int(min(canvas.size) * agent["radius"]))
            box = (max(0, min(agent["x"], x) - r), max(0, min(agent["y"], y) - r),
                   min(canvas.width, max(agent["x"], x) + r + 1),
                   min(canvas.height, max(agent["y"], y) + r + 1))
            patch = canvas.crop(box)
            if tool == "smudge":
                shifted = ImageChops.offset(patch, (x - agent["x"]) // 2, (y - agent["y"]) // 2)
                changed = Image.blend(patch, shifted.filter(ImageFilter.GaussianBlur(max(.5, r * .25))), .7)
            elif tool == "sharpen":
                changed = ImageOps.autocontrast(patch.filter(ImageFilter.UnsharpMask(radius=2, percent=230, threshold=2)))
            elif tool == "restore":
                changed = original.crop(box)
            elif tool == "sampler":
                donor = random.choice(donors)
                # Sample contiguous donor material with its proportions intact.
                fitted = ImageOps.fit(donor, canvas.size, Image.Resampling.LANCZOS)
                changed = fitted.crop(box)
            elif tool == "prism":
                channels = list(patch.split())
                c = random.randrange(3)
                channels[c] = ImageChops.offset(channels[c], max(1, r // 3), max(1, r // 5))
                changed = Image.merge("RGB", channels)
            elif tool == "contour ink":
                contours = ImageOps.autocontrast(patch.convert("L").filter(ImageFilter.FIND_EDGES))
                ink = ImageOps.invert(contours.point(lambda p: 255 if p > 30 else 0)).convert("RGB")
                changed = Image.blend(patch, ink, .72)
            else:
                pixels = np.asarray(patch).copy()
                order = np.argsort(pixels.sum(axis=2), axis=0, kind="stable")
                changed = Image.fromarray(np.take_along_axis(pixels, order[:, :, None], axis=0))
            mask = Image.new("L", patch.size)
            points = [(agent["x"] - box[0], agent["y"] - box[1]), (x - box[0], y - box[1])]
            draw = ImageDraw.Draw(mask)
            draw.line(points, fill=230, width=max(1, r * 2))
            for cx, cy in points:
                draw.ellipse((cx - r, cy - r, cx + r, cy + r), fill=230)
            mask = ImageChops.subtract(mask, protected.crop(box))
            canvas.paste(changed, box[:2], mask)
            agent["x"], agent["y"] = x, y
            labels.append("agent " + tool)
            # Local pixel values occasionally mutate an agent's next tool.
            if random.random() < .25:
                color = canvas.getpixel((x, y))
                agent["tool"] = TOOLS[sum(color) % len(TOOLS)]
                agent["radius"] = .02 + color[0] / 255 * .09
    canvas = Image.composite(before, canvas, protected)
    changed = ImageChops.difference(canvas, before).convert("L").point(lambda p: 255 if p else 0)
    result = canvas.resize(image.size, Image.Resampling.LANCZOS)
    mask = changed.resize(image.size, Image.Resampling.BILINEAR)
    return Image.composite(result, image, mask), list(dict.fromkeys(labels))
