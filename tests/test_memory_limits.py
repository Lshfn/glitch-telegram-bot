import json
import os
import random
import shutil
import subprocess
import sys
import tempfile
import tracemalloc
import unittest
from io import BytesIO
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import numpy as np
from PIL import Image

from artifact_memory import Artifact, ArtifactMemory
from content_fusion import project_bytes
from file_formats import ContentLimitError
from memory_effects import inherit_image
from test_content import pdf_bytes, wav_bytes
import glitch_bot as engine


class LongMemoryTests(unittest.TestCase):
    def test_months_old_memory_survives_restart_and_recall_does_not_extend_expiry(self):
        now = 1800000000
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "memory.sqlite3"
            memory = ArtifactMemory(path)
            with patch("artifact_memory.time.time", return_value=now - 150 * 86400):
                memory.remember("chat:user", "old", 2, Artifact(text=("old memory",)))
            reopened = ArtifactMemory(path)
            with patch("artifact_memory.time.time", return_value=now):
                level, donors = reopened.snapshot("chat:user", "new")
                self.assertEqual(donors[0].text, ("old memory",))
                self.assertEqual(reopened.snapshot("other:user", "")[1], [])
                with reopened._db() as db:
                    self.assertEqual(db.execute("SELECT touched FROM artifacts").fetchone()[0], now - 150 * 86400)
            with patch("artifact_memory.time.time", return_value=now + 31 * 86400):
                self.assertEqual(reopened.snapshot("chat:user", "")[1], [])

    def test_capacity_keeps_archived_files_as_new_uploads_arrive(self):
        with tempfile.TemporaryDirectory() as directory:
            memory = ArtifactMemory(Path(directory) / "memory.sqlite3", per_chat=8, total=100)
            now = 1800000000
            for i in range(80):
                with patch("artifact_memory.time.time", return_value=now + i), patch(
                        "artifact_memory.random.random", return_value=.999 if i == 0 else i / 1000):
                    memory.remember("a", str(i), 1, Artifact(text=(str(i),)))
            with patch("artifact_memory.time.time", return_value=now + 81):
                donors = memory.snapshot("a", "")[1]
            self.assertEqual(len(donors), 8)
            keys = {a.text[0] for a in donors}
            self.assertIn("0", keys)
            self.assertIn("79", keys)
            self.assertNotEqual(keys, {str(i) for i in range(72, 80)})

    def test_random_draws_include_old_middle_and_recent_history_without_crossing_users(self):
        now = 1800000000
        random.seed(18)
        with tempfile.TemporaryDirectory() as directory:
            memory = ArtifactMemory(Path(directory) / "memory.sqlite3", per_chat=120, total=1000, sample_size=8)
            for i in range(80):
                with patch("artifact_memory.time.time", return_value=now - i * 86400):
                    memory.remember("a", str(i), 1, Artifact(text=(str(i),)))
            with patch("artifact_memory.time.time", return_value=now):
                memory.remember("other", "private", 1, Artifact(text=("private",)))
                observed, draws = set(), set()
                for _ in range(20):
                    ages = [int(a.text[0]) for a in memory.snapshot("a", "0")[1]]
                    self.assertEqual(len(ages), 8)
                    self.assertEqual(len(set(ages)), 8)
                    self.assertNotIn(0, ages)
                    self.assertTrue(any(age < 20 for age in ages))
                    self.assertTrue(any(age >= 60 for age in ages))
                    observed.update(ages)
                    draws.add(tuple(ages))
                self.assertGreater(len(observed), 40)
                self.assertGreater(len(draws), 15)

    def test_repeated_upload_preserves_archive_age_and_priority_and_forget_erases_it(self):
        with tempfile.TemporaryDirectory() as directory:
            memory = ArtifactMemory(Path(directory) / "memory.sqlite3")
            with patch("artifact_memory.time.time", return_value=1800000000):
                memory.remember("a", "same", 1, Artifact(text=("original",)))
            with memory._db() as db:
                original = db.execute("SELECT created,priority FROM artifacts").fetchone()
            with patch("artifact_memory.time.time", return_value=1800001000):
                memory.remember("a", "same", 5, Artifact(text=("changed",)))
                with memory._db() as db:
                    self.assertEqual(db.execute("SELECT created,priority FROM artifacts").fetchone(), original)
                level, donors = memory.snapshot("a", "same")
                self.assertEqual(level, 6)
                self.assertEqual(donors[0].text, ("changed",))
                memory.forget("a")
                self.assertEqual(memory.snapshot("a", "same"), (1, []))

    def test_multiple_distinct_memories_can_share_one_image_without_geometric_masks(self):
        donors = []
        for color in ("red", "lime", "blue"):
            buf = BytesIO()
            Image.new("RGB", (48, 48), color).save(buf, "PNG")
            donors.append(Artifact(original_image=buf.getvalue()))
        counts, mixed = set(), False
        with patch("PIL.ImageDraw.ImageDraw.ellipse", side_effect=AssertionError("cutout")):
            for seed in range(40):
                random.seed(seed)
                result, labels = inherit_image(Image.new("RGB", (64, 64)), donors, .6)
                counts.add(len(labels))
                pixels = np.asarray(result)
                mixed |= sum(bool(pixels[:, :, c].max()) for c in range(3)) >= 2
        self.assertTrue({1, 2, 3} <= counts)
        self.assertTrue(mixed)


class LargerInputTests(unittest.IsolatedAsyncioTestCase):
    async def test_upload_above_old_ten_megabyte_limit_reaches_processor(self):
        data = b"x" * (11 * 1024 * 1024)
        upload = SimpleNamespace(file_size=len(data), file_id="upload", file_name="large.pdf")
        message = self.message(upload)
        bot = SimpleNamespace(get_file=AsyncMock(return_value=SimpleNamespace(file_path="large.pdf")),
                              download_file=AsyncMock(return_value=BytesIO(data)))
        processor = AsyncMock(return_value=(b"result", [], Artifact(), [], "large.pdf"))
        with tempfile.TemporaryDirectory() as directory:
            memory = ArtifactMemory(Path(directory) / "memory.sqlite3")
            with patch.object(engine, "bot", bot), patch.object(engine, "memory", memory), patch.object(
                    engine, "process_upload", processor):
                await engine.handle_file(message)
            self.assertEqual(len(processor.call_args.args[0]), len(data))
        message.answer.assert_not_awaited()
        message.reply_document.assert_awaited_once()

    @staticmethod
    def message(upload):
        return SimpleNamespace(chat=SimpleNamespace(id=1), from_user=SimpleNamespace(id=2), text=None,
                               photo=[], video=None, audio=None, voice=None, video_note=None, animation=None,
                               document=upload, answer=AsyncMock(), reply_document=AsyncMock())

    async def test_unknown_size_download_stops_at_limit_and_is_not_remembered(self):
        message = self.message(SimpleNamespace(file_size=None, file_id="upload", file_name="large.pdf"))

        async def download(path, destination, timeout):
            destination.write(b"x" * 32)
            destination.write(b"x" * 33)
            return destination

        bot = SimpleNamespace(get_file=AsyncMock(return_value=SimpleNamespace(file_path="large.pdf")), download_file=download)
        processor = AsyncMock()
        with tempfile.TemporaryDirectory() as directory:
            memory = ArtifactMemory(Path(directory) / "memory.sqlite3")
            with patch.object(engine, "bot", bot), patch.object(engine, "memory", memory), patch.object(
                    engine, "MAX_UPLOAD", 64), patch.object(engine, "process_upload", processor):
                await engine.handle_file(message)
            self.assertEqual(memory.snapshot("1:2", "")[1], [])
        processor.assert_not_awaited()
        message.reply_document.assert_not_awaited()
        message.answer.assert_awaited_once()

    async def test_configured_local_api_reads_shared_file_without_cloud_download(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "shared-file"
            path.write_bytes(b"local Telegram file")
            with patch.object(engine, "BOT_API_URL", "http://bot-api:8081"), patch.object(
                    engine, "BOT_API_LOCAL_FILES", True):
                bot = engine.make_bot("123456:" + "a" * 35)
            try:
                self.assertTrue(bot.session.api.is_local)
                output = await bot.download_file(path, destination=engine.UploadBuffer())
                self.assertEqual(output.read(), b"local Telegram file")
            finally:
                await bot.session.close()

    @unittest.skipUnless(shutil.which("pdfunite") and shutil.which("pdftoppm"), "Poppler not installed")
    async def test_pdf_above_old_twelve_page_limit_reopens_with_all_pages(self):
        output, _, _, _, name = await engine.process_upload(pdf_bytes(13), "long.pdf", 2, [])
        self.assertEqual(name, "long.pdf")
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "output.pdf"
            path.write_bytes(output)
            info = subprocess.check_output(["pdfinfo", str(path)]).decode()
        self.assertRegex(info, r"Pages:\s+13")

    @unittest.skipUnless(shutil.which("ffmpeg"), "FFmpeg not installed")
    async def test_audio_above_old_sixty_second_limit_reopens(self):
        import wave
        output, _, _, _, name = await engine.process_upload(wav_bytes(61), "long.wav", 2, [])
        self.assertEqual(name, "long.wav")
        with wave.open(BytesIO(output)) as decoded:
            self.assertEqual(decoded.getnframes(), 61 * 22050)

    @unittest.skipUnless(shutil.which("ffmpeg") and shutil.which("ffprobe"), "FFmpeg not installed")
    async def test_video_above_old_twelve_second_limit_retains_duration_and_audio(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "source.mp4"
            subprocess.run(["ffmpeg", "-nostdin", "-v", "error", "-y", "-f", "lavfi", "-i",
                            "testsrc2=size=96x64:rate=8", "-f", "lavfi", "-i", "sine=frequency=440:sample_rate=22050",
                            "-t", "20", "-c:v", "libx264", "-threads", "1", "-pix_fmt", "yuv420p",
                            "-c:a", "aac", str(source)], check=True)
            output, _, _, _, _ = await engine.process_upload(source.read_bytes(), "long.mp4", 2, [])
            target = root / "output.mp4"
            target.write_bytes(output)
            streams = json.loads(subprocess.check_output(["ffprobe", "-v", "error", "-show_entries",
                                                        "stream=codec_type,duration", "-of", "json", str(target)]))["streams"]
            self.assertEqual({s["codec_type"] for s in streams}, {"video", "audio"})
            self.assertTrue(all(abs(float(s["duration"]) - 20) < .15 for s in streams))
            subprocess.run(["ffmpeg", "-nostdin", "-v", "error", "-i", str(target), "-f", "null", "-"], check=True)


class ConfigurationAndProjectionTests(unittest.TestCase):
    def test_file_limit_configuration_matches_transport_capabilities(self):
        clean = dict(os.environ)
        for key in ("BOT_API_URL", "BOT_API_LOCAL_FILES", "MAX_UPLOAD_MB", "MAX_OUTPUT_MB"):
            clean.pop(key, None)
        command = [sys.executable, "-c", "import bot_settings as s; print(s.MAX_UPLOAD_MB,s.MAX_OUTPUT_MB)"]
        self.assertEqual(subprocess.check_output(command, env=clean).decode().strip(), "20 48")
        self.assertEqual(subprocess.check_output(command, env=dict(clean, BOT_API_URL="http://bot-api:8081")).decode().strip(), "100 100")
        rejected = subprocess.run(command, env=dict(clean, MAX_UPLOAD_MB="21"), capture_output=True)
        self.assertNotEqual(rejected.returncode, 0)
        self.assertIn(b"separately hosted Bot API", rejected.stderr)

    def test_byte_projection_reads_large_payload_with_bounded_extra_memory(self):
        payload = b"a" * (12 * 1024 * 1024)
        tracemalloc.start()
        try:
            first = project_bytes(payload)
            _, peak = tracemalloc.get_traced_memory()
        finally:
            tracemalloc.stop()
        second = project_bytes(payload[:-32768] + b"z" * 32768)
        self.assertTrue(first != second)
        self.assertLessEqual(len(first), 4096)
        self.assertLess(peak, 4 * 1024 * 1024)
