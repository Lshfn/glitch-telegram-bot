"""Local OCR on native Python hosts without a system Tesseract executable."""
import csv
import hashlib
import io
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import urllib.request


MODEL_REVISION = "87416418657359cb625c412a48b6e1d6d41c29bd"
MODEL_HASHES = {
    "eng": "7d4322bd2a7749724879683fc3912cb542f19906c83bcc1a52132556427170b2",
    "rus": "e16e5e036cce1d9ec2b00063cf8b54472625b9e14d893a169e2b0dedeb4df225",
}
MODEL_LIMIT = 8 * 1024 * 1024
model_lock = threading.Lock()


def ensure_models():
    languages = [lang for lang in os.getenv("OCR_LANGUAGES", "eng+rus").split("+")
                 if lang in MODEL_HASHES]
    if not languages:
        raise RuntimeError("Portable OCR supports the English and Russian language packs")
    folder = Path(os.getenv("OCR_DATA_DIR", Path(__file__).parent / "data" / "tessdata"))
    with model_lock:
        folder.mkdir(parents=True, exist_ok=True)
        for lang in dict.fromkeys(languages):
            path = folder / f"{lang}.traineddata"
            expected = MODEL_HASHES[lang]
            if path.exists() and path.stat().st_size <= MODEL_LIMIT:
                if hashlib.sha256(path.read_bytes()).hexdigest() == expected:
                    continue
            url = ("https://raw.githubusercontent.com/tesseract-ocr/tessdata_fast/"
                   f"{MODEL_REVISION}/{lang}.traineddata")
            with urllib.request.urlopen(url, timeout=12) as response:
                data = response.read(MODEL_LIMIT + 1)
            if len(data) > MODEL_LIMIT or hashlib.sha256(data).hexdigest() != expected:
                raise RuntimeError("OCR language pack failed integrity verification")
            with tempfile.NamedTemporaryFile(dir=folder, delete=False) as temporary:
                pending = Path(temporary.name)
                try:
                    temporary.write(data)
                    temporary.close()
                    pending.replace(path)
                finally:
                    pending.unlink(missing_ok=True)
    return str(folder), "+".join(dict.fromkeys(languages))


def read_data(image):
    try:
        folder, languages = ensure_models()
        data = io.BytesIO()
        image.save(data, format="PNG")
        result = subprocess.run(
            [sys.executable, str(Path(__file__).resolve()), folder, languages],
            input=data.getvalue(), stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
            timeout=8, check=True,
            env={"PATH": os.environ.get("PATH", ""), "LANG": "C.UTF-8", "OMP_THREAD_LIMIT": "1"},
        )
        fields = ("level", "page_num", "block_num", "par_num", "line_num", "word_num",
                  "left", "top", "width", "height", "conf", "text")
        rows = list(csv.DictReader(io.StringIO(result.stdout.decode("utf-8")),
                                   fieldnames=fields, delimiter="\t", quoting=csv.QUOTE_NONE))
        keys = ("text", "conf", "page_num", "block_num", "par_num", "line_num",
                "left", "top", "width", "height")
        return {key: [row[key] for row in rows] for key in keys}
    except (OSError, ValueError, KeyError, subprocess.SubprocessError) as exc:
        raise RuntimeError("Local OCR unavailable") from exc


if __name__ == "__main__":
    # Isolate native recognition from the bot, with a hard parent-side timeout.
    if sys.platform != "win32":
        import resource
        resource.setrlimit(resource.RLIMIT_CPU, (8, 8))
        resource.setrlimit(resource.RLIMIT_AS, (1024 ** 3, 1024 ** 3))
    from PIL import Image
    from tesserocr import PyTessBaseAPI, PSM, OEM
    image = Image.open(io.BytesIO(sys.stdin.buffer.read(20 * 1024 * 1024)))
    if image.width * image.height > 12_000_000:
        raise ValueError("OCR image too large")
    with PyTessBaseAPI(path=sys.argv[1], lang=sys.argv[2],
                       psm=PSM.SPARSE_TEXT, oem=OEM.LSTM_ONLY) as api:
        api.SetImage(image.convert("RGB"))
        sys.stdout.write(api.GetTSVText(0))
