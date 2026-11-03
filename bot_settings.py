"""Owner configuration shared by the bot and its isolated workers."""
import os
from urllib.parse import urlparse

MIB = 1024 * 1024


def integer(name, default, minimum=1, maximum=100000):
    try:
        value = int(os.getenv(name, str(default)))
    except ValueError:
        raise ValueError(f"{name} must be an integer") from None
    if not minimum <= value <= maximum:
        raise ValueError(f"{name} must be between {minimum} and {maximum}")
    return value


BOT_API_URL = os.getenv("BOT_API_URL", "").rstrip("/")
BOT_API_LOCAL_FILES = os.getenv("BOT_API_LOCAL_FILES", "true" if BOT_API_URL else "").lower() in ("1", "true", "yes")
if BOT_API_URL:
    parsed = urlparse(BOT_API_URL)
    if parsed.scheme not in ("http", "https") or not parsed.hostname or parsed.username or parsed.password:
        raise ValueError("BOT_API_URL must be an HTTP(S) server base URL without credentials")
elif BOT_API_LOCAL_FILES:
    raise ValueError("BOT_API_LOCAL_FILES requires BOT_API_URL and a shared server files directory")

MAX_UPLOAD_MB = integer("MAX_UPLOAD_MB", 100 if BOT_API_URL else 20, maximum=200)
MAX_OUTPUT_MB = integer("MAX_OUTPUT_MB", 100 if BOT_API_URL else 48, maximum=200)
if not BOT_API_URL and (MAX_UPLOAD_MB > 20 or MAX_OUTPUT_MB > 48):
    raise ValueError("Larger file limits require a separately hosted Bot API server via BOT_API_URL")
MAX_UPLOAD = MAX_UPLOAD_MB * MIB
MAX_OUTPUT = MAX_OUTPUT_MB * MIB
MAX_PAGES = integer("MAX_PDF_PAGES", 100, maximum=200)
MAX_PDF_TEXT = 2 * MIB
MAX_AUDIO_SECONDS = integer("MAX_AUDIO_SECONDS", 180, maximum=300)
MAX_VIDEO_SECONDS = integer("MAX_VIDEO_SECONDS", 120, maximum=300)
MAX_VIDEO_FRAMES = 320
WORKER_WALL_SECONDS = 240
WORKER_CPU_SECONDS = 180

MEMORY_DAYS = integer("MEMORY_DAYS", 180, maximum=3650)
MEMORY_PER_HISTORY = integer("MEMORY_PER_HISTORY", 512)
MEMORY_TOTAL = integer("MEMORY_TOTAL", 8192)
MEMORY_DONORS = integer("MEMORY_DONORS", 16, maximum=32)
