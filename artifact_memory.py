"""Bounded, chat-local traces of previous uploads. Never store original files."""
import hashlib
import json
import random
import sqlite3
import time
from contextlib import contextmanager
from dataclasses import dataclass
from io import BytesIO
from pathlib import Path
from threading import RLock

from PIL import Image, ImageOps


@dataclass(frozen=True)
class Artifact:
    image: bytes = b""
    text: tuple[str, ...] = ()
    sample: bytes = b""


def open_image(data):
    with Image.open(BytesIO(data)) as source:
        if source.width * source.height > 12_000_000:
            raise ValueError("Image exceeds 12 megapixels")
        image = ImageOps.exif_transpose(source).convert("RGB")
    image.thumbnail((1600, 1600))
    return image


def extract_artifact(data, text="", image=False):
    thumbnail = b""
    if image:
        img = open_image(data)
        img.thumbnail((320, 320))
        buf = BytesIO()
        img.save(buf, "JPEG", quality=70)  # no EXIF or original filename
        thumbnail = buf.getvalue()
    words = text[:16000].split()
    snippets = []
    for _ in range(min(5, max(1, len(words) // 8))):
        if not words:
            break
        start = random.randrange(len(words))
        snippets.append(" ".join(words[start:start + random.randint(3, 16)])[:240])
    # Keep a few payload fragments, not the original header or entire upload.
    sample = b""
    if len(data) > 64:
        lo, hi = len(data) // 5, len(data) * 4 // 5
        for _ in range(4):
            start = random.randint(lo, hi)
            sample += data[start:min(hi, start + 1024)]
    return Artifact(thumbnail, tuple(snippets), sample[:4096])


class ArtifactMemory:
    def __init__(self, path, per_chat=24, total=256, ttl=7 * 86400):
        self.path = str(path)
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        self.per_chat, self.total, self.ttl = per_chat, total, ttl
        self.lock = RLock()
        with self._db() as db:
            db.executescript("""
                CREATE TABLE IF NOT EXISTS artifacts (
                    chat TEXT NOT NULL, digest TEXT NOT NULL, touched REAL NOT NULL,
                    level INTEGER NOT NULL, image BLOB NOT NULL, text TEXT NOT NULL,
                    sample BLOB NOT NULL, PRIMARY KEY(chat, digest));
                CREATE TABLE IF NOT EXISTS preferences (
                    chat TEXT PRIMARY KEY, translate_enabled INTEGER NOT NULL,
                    touched REAL NOT NULL);
            """)

    @contextmanager
    def _db(self):
        db = sqlite3.connect(self.path, timeout=10)
        try:
            db.execute("PRAGMA secure_delete=ON")
            with db:
                yield db
        finally:
            db.close()

    def _prune(self, db):
        db.execute("DELETE FROM artifacts WHERE touched < ?", (time.time() - self.ttl,))
        db.execute("DELETE FROM artifacts WHERE rowid NOT IN "
                   "(SELECT rowid FROM artifacts ORDER BY touched DESC LIMIT ?)", (self.total,))
        db.execute("DELETE FROM preferences WHERE touched < ?", (time.time() - self.ttl,))
        db.execute("DELETE FROM preferences WHERE rowid NOT IN "
                   "(SELECT rowid FROM preferences ORDER BY touched DESC LIMIT 512)")

    @staticmethod
    def digest(data):
        return hashlib.sha256(data).hexdigest()

    def snapshot(self, chat, digest):
        with self.lock, self._db() as db:
            self._prune(db)
            row = db.execute("SELECT level FROM artifacts WHERE chat=? AND digest=?",
                             (str(chat), digest)).fetchone()
            rows = db.execute("SELECT image,text,sample FROM artifacts WHERE chat=? "
                              "ORDER BY touched DESC LIMIT ?", (str(chat), self.per_chat)).fetchall()
        return min(12, (row[0] if row else 0) + 1), [
            Artifact(image, tuple(json.loads(text)), sample) for image, text, sample in rows]

    def remember(self, chat, digest, level, artifact):
        with self.lock, self._db() as db:
            db.execute("INSERT OR REPLACE INTO artifacts VALUES (?,?,?,?,?,?,?)",
                       (str(chat), digest, time.time(), level, artifact.image,
                        json.dumps(artifact.text, ensure_ascii=False), artifact.sample))
            db.execute("DELETE FROM artifacts WHERE chat=? AND rowid NOT IN "
                       "(SELECT rowid FROM artifacts WHERE chat=? ORDER BY touched DESC LIMIT ?)",
                       (str(chat), str(chat), self.per_chat))
            self._prune(db)

    def translations(self, chat, enabled=None):
        with self.lock, self._db() as db:
            self._prune(db)
            if enabled is not None:
                db.execute("INSERT OR REPLACE INTO preferences VALUES (?,?,?)",
                           (str(chat), int(enabled), time.time()))
            row = db.execute("SELECT translate_enabled FROM preferences WHERE chat=?", (str(chat),)).fetchone()
        return bool(row and row[0])

    def forget(self, chat):
        with self.lock, self._db() as db:
            db.execute("DELETE FROM artifacts WHERE chat=?", (str(chat),))
            db.execute("DELETE FROM preferences WHERE chat=?", (str(chat),))
