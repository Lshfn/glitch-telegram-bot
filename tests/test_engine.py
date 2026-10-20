import asyncio
import os
import random
import shutil
import tempfile
import unittest
from io import BytesIO
from pathlib import Path
from unittest.mock import patch

from aiohttp import web, ClientSession
from PIL import Image, ImageDraw, ImageFont

from artifact_memory import Artifact, ArtifactMemory, extract_artifact
from language_glitch import Translator, TextRegion, decode_text, paint_translation, read_regions
from memory_effects import inherit_image, inherit_bytes, glitch_text

# Importing the engine does not start polling or require a real bot token.
_temp = tempfile.TemporaryDirectory()
with patch.dict(os.environ, {"BOT_TOKEN": "", "MEMORY_DB": str(Path(_temp.name) / "engine.sqlite3")}):
    import glitch_bot as engine


def png(color="red", size=(240, 160)):
    buf = BytesIO()
    Image.new("RGB", size, color).save(buf, "PNG")
    return buf.getvalue()


class MemoryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.path = Path(self.temp.name) / "memory.sqlite3"
        self.memory = ArtifactMemory(self.path, per_chat=2, total=3)

    def tearDown(self):
        self.temp.cleanup()

    def test_isolation_restarts_repeat_and_forget(self):
        artifact = Artifact(text=("previous phrase",), sample=b"abc")
        self.memory.remember("chat1:user1", "a", 1, artifact)
        self.memory.translations("chat1:user1", True)
        reopened = ArtifactMemory(self.path)
        self.assertEqual(reopened.snapshot("chat1:user1", "a"), (2, [artifact]))
        for scope in ("chat2:user1", "chat1:user2"):
            self.assertEqual(reopened.snapshot(scope, "a"), (1, []))
            self.assertFalse(reopened.translations(scope))
        reopened.forget("chat1:user1")
        self.assertEqual(reopened.snapshot("chat1:user1", "a"), (1, []))
        self.assertFalse(reopened.translations("chat1:user1"))

    def test_per_chat_and_global_bounds(self):
        for i in range(5):
            self.memory.remember("a", str(i), 1, Artifact(text=(str(i),)))
        self.assertEqual(len(self.memory.snapshot("a", "")[1]), 2)
        for i in range(5):
            self.memory.remember("b" + str(i), str(i), 1, Artifact())
        with self.memory._db() as db:
            self.assertEqual(db.execute("SELECT count(*) FROM artifacts").fetchone()[0], 3)

    def test_expiry(self):
        with patch("artifact_memory.time.time", return_value=100):
            self.memory.remember("a", "a", 1, Artifact(text=("old",)))
        with patch("artifact_memory.time.time", return_value=100 + 8 * 86400):
            self.assertEqual(self.memory.snapshot("a", "a"), (1, []))

    def test_extracted_trace_is_bounded(self):
        a = extract_artifact(png(size=(1000, 500)), "word " * 2000, True)
        image = Image.open(BytesIO(a.image))
        self.assertLessEqual(max(image.size), 320)
        self.assertLessEqual(len(a.sample), 4096)
        self.assertLessEqual(len(a.text), 5)
        self.assertTrue(all(len(t) <= 240 for t in a.text))


class EffectTests(unittest.TestCase):
    def test_image_and_binary_traces_change_image(self):
        for artifact in (Artifact(image=png("red")), Artifact(sample=b"\xff" * 768)):
            random.seed(7)
            out, labels = inherit_image(Image.new("RGB", (80, 60), "black"), [artifact], .5)
            self.assertNotEqual(out.tobytes(), Image.new("RGB", (80, 60), "black").tobytes())
            self.assertTrue(labels)

    def test_binary_graft_preserves_length_header_and_trailer(self):
        random.seed(3)
        data = b"A" * 1000
        result, labels = inherit_bytes(data, [Artifact(sample=b"B" * 100)], 4)
        self.assertEqual(len(result), len(data))
        self.assertEqual(result[:200], data[:200])
        self.assertEqual(result[800:], data[800:])
        self.assertIn(b"B", result)
        self.assertEqual(labels, ["inherited bytes"])

    def test_unicode_and_cross_format_text(self):
        random.seed(4)
        text, labels = glitch_text("Hello\nПривет\n", [Artifact(text=("старый призрак",))], 3)
        self.assertIn("старый призрак", text)
        text.encode("utf-8").decode("utf-8")
        self.assertIn("inherited text", labels)
        text, labels = glitch_text("hello", [Artifact(sample=b"\xff\xab")], 1)
        self.assertIn("ff ab", text)

    def test_image_outputs_and_recipe_variety(self):
        recipes = set()
        donors = [Artifact(image=png("blue"))]
        for seed in range(8):
            random.seed(seed)
            result, recipe = engine.glitch_image(png(), 1 + seed, "PNG", donors)
            Image.open(BytesIO(result)).load()
            recipes.add(tuple(recipe))
            self.assertTrue(any(name.startswith("inherited") for name in recipe))
        self.assertGreater(len(recipes), 6)
        result, _ = engine.glitch_image(png(size=(1, 1)), 12, "JPEG")
        Image.open(BytesIO(result)).load()

    def test_text_encodings(self):
        for data, expected in (("Привет".encode("cp1251"), "Привет"),
                               ("hello".encode("utf-16"), "hello"),
                               ("Καλημέρα".encode("utf-8-sig"), "Καλημέρα")):
            self.assertEqual(decode_text(data)[0], expected)

    @unittest.skipUnless(shutil.which("tesseract"), "Tesseract not installed")
    def test_real_local_ocr_and_region_replacement(self):
        image = Image.new("RGB", (600, 120), "white")
        face = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", 36)
        ImageDraw.Draw(image).text((20, 30), "HELLO WORLD", font=face, fill="black")
        with patch.dict(os.environ, {"OCR_LANGUAGES": "eng"}):
            regions = read_regions(image)
        self.assertTrue(any("HELLO" in r.text for r in regions))
        before = image.tobytes()
        paint_translation(image, regions[0], "Привет мир")
        self.assertNotEqual(before, image.tobytes())


class TranslationTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.requests = []
        self.fail = False
        self.malformed = False
        async def translate(request):
            data = await request.json()
            self.requests.append(data)
            if self.malformed:
                return web.json_response(["invalid response"])
            if self.fail:
                return web.json_response({"error": "unavailable"}, status=503)
            return web.json_response({"translatedText": "bonjour fantôme", "detectedLanguage": {"language": "en"}})
        app = web.Application()
        app.router.add_post("/translate", translate)
        self.runner = web.AppRunner(app)
        await self.runner.setup()
        site = web.TCPSite(self.runner, "127.0.0.1", 0)
        await site.start()
        port = site._server.sockets[0].getsockname()[1]
        self.translator = Translator(f"http://127.0.0.1:{port}", "test-key", ["fr"])

    async def asyncTearDown(self):
        await self.runner.cleanup()

    async def test_real_http_contract_and_text_replacement(self):
        async with ClientSession() as session:
            result, targets, failures = await self.translator.replace_spans("Hello world.\nNext line\n", session)
        self.assertIn("bonjour fantôme", result)
        self.assertTrue(targets)
        self.assertEqual(failures, 0)
        for request in self.requests:
            self.assertEqual(request["source"], "auto")
            self.assertEqual(request["target"], "fr")
            self.assertEqual(request["api_key"], "test-key")

    async def test_image_text_translation(self):
        image = Image.new("RGB", (300, 60), "white")
        region = TextRegion((0, 0, 290, 50), "hello world")
        async with ClientSession() as session:
            image, targets, failures = await self.translator.replace_regions(image, [region], session)
        self.assertEqual(targets, ["fr"])
        self.assertEqual(failures, 0)
        self.assertNotEqual(image.tobytes(), Image.new("RGB", image.size, "white").tobytes())

    async def test_provider_failure_keeps_file_and_inheritance(self):
        self.fail = True
        with patch.object(engine, "translator", self.translator):
            result, recipe, artifact, notices, _ = await engine.process_upload(
                b"Hello world.\nSecond phrase.\n", "sample.txt", 1,
                [Artifact(text=("old donor",))], True)
        self.assertIn("old donor", result.decode("utf-8"))
        self.assertIn("inherited text", recipe)
        self.assertTrue(notices)
        self.assertTrue(artifact.text)

    async def test_translation_off_sends_no_requests(self):
        with patch.object(engine, "translator", self.translator):
            result, *_ = await engine.process_upload(b"Hello world.", "a.txt", 1, [], False)
        self.assertTrue(result)
        self.assertEqual(self.requests, [])

    async def test_malformed_provider_reply_is_a_recoverable_failure(self):
        self.malformed = True
        async with ClientSession() as session:
            text, targets, failures = await self.translator.replace_spans("Hello world.", session)
        self.assertEqual(text, "Hello world.")
        self.assertEqual(targets, [])
        self.assertEqual(failures, 1)

    async def test_complete_image_translation_and_memory_pipeline(self):
        region = TextRegion((10, 10, 200, 40), "hello world")
        with patch.object(engine, "translator", self.translator), patch.object(engine, "read_regions", return_value=[region]):
            result, recipe, artifact, notices, filename = await engine.process_upload(
                png("white"), "sample.png", 2, [Artifact(image=png("blue"))], True)
        Image.open(BytesIO(result)).load()
        self.assertEqual(notices, [])
        self.assertEqual(filename, "sample.png")
        self.assertTrue(artifact.image)
        self.assertIn("translations: fr", recipe)
        self.assertTrue(any(s.startswith("inherited") for s in recipe))
