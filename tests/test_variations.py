import os
import random
import shutil
import subprocess
import tempfile
import unittest
from io import BytesIO
from pathlib import Path
from unittest.mock import patch

import numpy as np
from PIL import Image, ImageChops, ImageDraw, ImageOps

from artifact_memory import Artifact, ArtifactMemory
from content_fusion import merge_traces, trace
from file_formats import codec_datamosh, pack_artifact, unpack_artifact
from image_agents import agent_canvas
from memory_effects import inherit_image
from image_surfaces import content_mask, multiscale_blend
from video_mosh import motion_feedback, remove_intra_frames

_temp = tempfile.TemporaryDirectory()
with patch.dict(os.environ, {"BOT_TOKEN": "", "MEMORY_DB": str(Path(_temp.name) / "engine.sqlite3")}):
    import glitch_bot as engine


def encoded(image):
    buf = BytesIO()
    image.save(buf, "PNG")
    return buf.getvalue()


class ImageVariationTests(unittest.TestCase):
    def test_original_and_processed_thumbnails_survive_database_and_worker_transfer(self):
        source = trace(image=Image.new("RGB", (240, 80), "cyan"))
        rendered = trace(image=Image.new("RGB", (80, 240), "magenta"))
        artifact = merge_traces(source, rendered)
        self.assertEqual(artifact.original_image, source.image)
        self.assertEqual(artifact.image, rendered.image)
        self.assertEqual(unpack_artifact(pack_artifact(artifact)), artifact)
        with tempfile.TemporaryDirectory() as root:
            path = Path(root) / "memory.sqlite3"
            ArtifactMemory(path).remember("1:2", "upload", 8, artifact)
            recovered = ArtifactMemory(path).snapshot("1:2", "upload")[1][0]
            self.assertEqual(recovered, artifact)
            self.assertEqual(ArtifactMemory(path).snapshot("1:3", "upload")[1], [])
            ArtifactMemory(path).forget("1:2")
            self.assertEqual(ArtifactMemory(path).snapshot("1:2", "upload"), (1, []))

    def test_inherited_window_keeps_source_aspect_ratio_and_intact_colors(self):
        donor = Image.new("RGB", (240, 80), "cyan")
        ImageDraw.Draw(donor).rectangle((0, 0, 40, 79), fill="red")
        artifact = Artifact(image=encoded(Image.new("RGB", donor.size, "magenta")),
                            original_image=encoded(donor))
        random.seed(42)
        with patch("memory_effects.random.randint", return_value=1):
            result, labels = inherit_image(Image.new("RGB", (400, 400)), [artifact], .8, style="window")
        box = result.getbbox()
        ratio = (box[2] - box[0]) / (box[3] - box[1])
        self.assertAlmostEqual(ratio, 3, delta=.1)
        pixels = np.asarray(result)
        self.assertTrue(np.any(np.all(pixels == (0, 255, 255), axis=2)))
        self.assertTrue(np.any(np.all(pixels == (255, 0, 0), axis=2)))
        self.assertFalse(np.any(np.all(pixels == (255, 0, 255), axis=2)))
        self.assertIn("inherited window", labels)

    def test_memory_styles_and_agents_handle_tiny_and_rectangular_images(self):
        donor = Artifact(image=encoded(Image.new("RGB", (31, 9), "orange")), text=("remembered words",))
        for size in ((1, 1), (7, 11), (120, 80)):
            for style in ("fragment", "window", "ghost", "contour", "difference", "channel", "splice", "recursive",
                          "palimpsest", "texture graft", "ghost field", "channel haunt", "recursive field"):
                result, _ = inherit_image(Image.new("RGB", size), [donor], .9, style=style)
                self.assertEqual(result.size, size)
                self.assertEqual(result.mode, "RGB")
            result, labels = agent_canvas(Image.new("RGB", size, "navy"), Image.new("RGB", size, "red"), [donor], 12)
            self.assertEqual(result.size, size)
            self.assertIn("protected detail", labels)

    def test_agents_change_local_regions_and_react_to_donor_content(self):
        source = Image.new("RGB", (240, 160), "#304050")
        ImageDraw.Draw(source).ellipse((60, 20, 140, 110), fill="yellow")
        results = []
        for color in ("red", "cyan"):
            random.seed(11)
            donor = Artifact(image=encoded(Image.new("RGB", source.size, color)))
            result, labels = agent_canvas(source, source, [donor], 9, rounds=10)
            self.assertIn("agent sampler", labels)
            changed = np.any(np.asarray(source) != np.asarray(result), axis=2)
            self.assertGreater(float(changed.mean()), .01)
            self.assertLess(float(changed.mean()), .8)
            results.append(result.tobytes())
        self.assertNotEqual(*results)

    def test_automatic_recipes_exclude_strips_and_rectangular_cuts(self):
        random.seed(77)
        seen = set()
        for _ in range(600):
            names, strength = engine.make_glitch_recipe(12)
            seen.update(names)
            self.assertFalse(set(names) & (engine.STRIP_EFFECTS | {"block displacement", "channel blocks", "noise clusters"}))
            self.assertTrue(0 < strength <= 1)
        self.assertEqual(set(engine.AUTOMATIC_EFFECTS), seen)
        self.assertTrue(engine.STRIP_EFFECTS <= set(engine.EFFECTS))

    def test_region_selection_changes_with_image_structure(self):
        source = Image.new("RGB", (144, 96), "#304050")
        ImageDraw.Draw(source).polygon([(10, 0), (95, 0), (118, 20), (72, 48), (110, 96),
                                      (30, 96), (42, 66), (8, 43)], fill="#dca35a")
        masks = []
        for image in (source, ImageOps.mirror(source)):
            random.seed(43)
            mask = content_mask(image, coverage=.3)
            pixels = np.asarray(mask)
            self.assertGreater(float(pixels.mean()), 5)
            self.assertLess(float(pixels.mean()), 120)
            self.assertTrue(np.any((pixels > 0) & (pixels < 255)))
            masks.append(pixels)
        self.assertFalse(np.array_equal(*masks))

    def test_automatic_memory_does_not_reintroduce_old_processed_cutouts(self):
        artifact = Artifact(image=encoded(Image.new("RGB", (80, 60), "magenta")),
                            original_image=encoded(Image.new("RGB", (80, 60), "cyan")))
        for seed in range(20):
            random.seed(seed)
            result, labels = inherit_image(Image.new("RGB", (120, 80)), [artifact], .8)
            self.assertNotIn("inherited recursive field", labels)
            self.assertEqual(int(np.asarray(result)[:, :, 0].max()), 0)

    def test_multiscale_memory_changes_region_and_keeps_untouched_pixels(self):
        base = Image.new("RGB", (160, 96), "#304050")
        donor = Image.new("RGB", base.size, "#daaa77")
        ImageDraw.Draw(donor).text((20, 40), "EARLIER FILE", fill="black")
        random.seed(8)
        mask = content_mask(base, donor, .35)
        result = multiscale_blend(base, donor, mask)
        before, after = np.asarray(base), np.asarray(result)
        untouched = np.asarray(mask) <= 1
        self.assertTrue(untouched.any())
        self.assertTrue(np.array_equal(before[untouched], after[untouched]))
        self.assertGreater(float(np.abs(before.astype(float) - after).mean()), 1)

    def test_automatic_images_never_draw_ellipses_or_use_jpeg_byte_corruption(self):
        source = Image.new("RGB", (120, 80), "#304050")
        ImageDraw.Draw(source).text((10, 20), "CURRENT", fill="white")
        donor = Artifact(image=encoded(ImageOps.invert(source)), text=("earlier file",))
        with patch("PIL.ImageDraw.ImageDraw.ellipse", side_effect=AssertionError("geometric cutout")), patch.object(
                engine, "jpeg_databend", side_effect=AssertionError("JPEG byte corruption")):
            for seed in range(24):
                random.seed(seed)
                result, recipe = engine.glitch_image(b"", 1 + seed % 12,
                                                      "JPEG" if seed % 2 else "PNG", [donor], source)
                Image.open(BytesIO(result)).load()
                self.assertFalse(set(recipe) & engine.STRIP_EFFECTS)
                self.assertNotIn("modular lattice", recipe)
                self.assertNotIn("spectral crossover", recipe)
                self.assertNotIn("agent pixel trail", recipe)


class DatamoshTests(unittest.TestCase):
    def test_intra_removal_preserves_first_keyframe_and_predictive_payloads(self):
        prefix = b"\x00\x00\x01\x20VOL"
        i_frame = b"\x00\x00\x01\xb6\x00INTRA"
        p_frame = b"\x00\x00\x01\xb6\x40PREDICTIVE"
        data = prefix + i_frame + p_frame + prefix + i_frame + p_frame
        result, count = remove_intra_frames(data)
        self.assertEqual(count, 1)
        self.assertEqual(result.count(i_frame), 1)
        self.assertEqual(result.count(p_frame), 2)
        self.assertEqual(result.count(prefix), 2)
        self.assertEqual(remove_intra_frames(b"not a stream"), (b"not a stream", 0))

    def test_motion_feedback_follows_actual_translation_and_is_bounded(self):
        rng = np.random.default_rng(8)
        previous = Image.fromarray(rng.integers(0, 256, (96, 96, 3), dtype=np.uint8))
        current = ImageChops.offset(previous, 2, 0)
        feedback = Image.new("RGB", previous.size, "black")
        ImageDraw.Draw(feedback).rectangle((28, 25, 60, 65), fill="cyan")
        output = motion_feedback(current, previous, current, feedback, .9)
        self.assertEqual(output.size, current.size)
        shifted = ImageChops.offset(feedback, 2, 0)
        error = np.mean(np.abs(np.asarray(output, dtype=float) - np.asarray(shifted, dtype=float)))
        still_error = np.mean(np.abs(np.asarray(output, dtype=float) - np.asarray(feedback, dtype=float)))
        self.assertLess(error, still_error)
        for size in ((1, 1), (7, 11)):
            tiny = current.resize(size)
            self.assertEqual(motion_feedback(tiny, tiny, tiny, tiny, 1, "bloom").size, size)

    @unittest.skipUnless(shutil.which("ffmpeg") and shutil.which("ffprobe"), "FFmpeg not installed")
    def test_actual_codec_datamosh_preserves_short_and_maximum_length_clips(self):
        for count in (24, 96):
            with self.subTest(frames=count), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                frames = root / "frames"
                frames.mkdir()
                for i in range(count):
                    image = Image.new("RGB", (160, 96), "#283848")
                    draw = ImageDraw.Draw(image)
                    x = i * 4 % 110
                    draw.rectangle((x, 10, x + 45, 75), fill="red" if i < count // 2 else "cyan")
                    image.save(frames / f"{i + 1:04d}.png")
                target, used = codec_datamosh(frames, root, count)
                self.assertTrue(used)
                self.assertEqual(len(list(target.glob("*.png"))), count)
                types = subprocess.check_output(["ffprobe", "-v", "error", "-show_entries", "frame=pict_type",
                                                 "-of", "csv=p=0", str(root / "moshed.m4v")]).decode().split()
                self.assertEqual(types[0], "I")
                self.assertEqual(types.count("I"), 1)
                self.assertIn("P", types)
                sample = f"{count // 2 + 2:04d}.png"
                original = np.asarray(Image.open(frames / sample), dtype=float)
                damaged = np.asarray(Image.open(target / sample), dtype=float)
                self.assertGreater(float(np.abs(original - damaged).mean()), 10)
