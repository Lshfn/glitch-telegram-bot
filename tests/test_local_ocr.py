import hashlib
import io
import os
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

from PIL import Image, ImageDraw, ImageFont

import local_ocr
from language_glitch import read_regions


class ModelTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        environment = patch.dict(os.environ, {"OCR_DATA_DIR": self.temp.name, "OCR_LANGUAGES": "eng"})
        environment.start()
        self.addCleanup(environment.stop)

    def test_verified_model_is_downloaded_once_and_corrupt_cache_is_replaced(self):
        data = b"test model bytes"
        digest = hashlib.sha256(data).hexdigest()
        with patch.dict(local_ocr.MODEL_HASHES, {"eng": digest}), patch(
                "local_ocr.urllib.request.urlopen", side_effect=lambda *a, **k: io.BytesIO(data)) as fetch:
            folder, languages = local_ocr.ensure_models()
            self.assertEqual(languages, "eng")
            path = Path(folder) / "eng.traineddata"
            self.assertEqual(path.read_bytes(), data)
            local_ocr.ensure_models()
            self.assertEqual(fetch.call_count, 1)
            self.assertIn(local_ocr.MODEL_REVISION, fetch.call_args.args[0])
            path.write_bytes(b"broken cache")
            local_ocr.ensure_models()
            self.assertEqual(path.read_bytes(), data)
            self.assertEqual(fetch.call_count, 2)

    def test_bad_model_is_rejected_without_installing_it(self):
        with patch("local_ocr.urllib.request.urlopen", return_value=io.BytesIO(b"invalid")):
            with self.assertRaisesRegex(RuntimeError, "integrity"):
                local_ocr.ensure_models()
        self.assertFalse((Path(self.temp.name) / "eng.traineddata").exists())

    def test_hung_ocr_becomes_a_recoverable_error(self):
        with patch("local_ocr.ensure_models", return_value=(self.temp.name, "eng")), patch(
                "local_ocr.subprocess.run", side_effect=subprocess.TimeoutExpired("ocr", 8)):
            with self.assertRaisesRegex(RuntimeError, "unavailable"):
                local_ocr.read_data(Image.new("RGB", (100, 40), "white"))

    def test_real_portable_engine_reads_english_and_russian_without_executable(self):
        folder = Path(os.getenv("GLITCH_TEST_TESSDATA", "/usr/share/tesseract-ocr/5/tessdata"))
        if not all((folder / f"{lang}.traineddata").exists() for lang in ("eng", "rus")):
            self.skipTest("Install English/Russian Tesseract data for the integration test")
        image = Image.new("RGB", (1000, 220), "white")
        draw = ImageDraw.Draw(image)
        face = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", 40)
        draw.text((20, 20), "The image remembers the file.", font=face, fill="black")
        draw.text((20, 110), "Память хранит предыдущий файл.", font=face, fill="black")
        with patch("language_glitch.shutil.which", return_value=None), patch(
                "local_ocr.ensure_models", return_value=(str(folder), "eng+rus")):
            regions = read_regions(image)
        text = " ".join(region.text for region in regions)
        self.assertIn("image remembers", text)
        self.assertIn("Память", text)
        for region in regions:
            x0, y0, x1, y1 = region.box
            self.assertTrue(0 <= x0 < x1 <= image.width)
            self.assertTrue(0 <= y0 < y1 <= image.height)


if __name__ == "__main__":
    unittest.main()
