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
from bot_settings import MEMORY_DAYS, MEMORY_PER_HISTORY, MEMORY_TOTAL, MEMORY_DONORS


@dataclass(frozen=True)
class Artifact:
    image: bytes = b""
    text: tuple[str, ...] = ()
    sample: bytes = b""
    signal: bytes = b""  # at most 2048 int16 values derived from decoded content
    original_image: bytes = b""  # separate thumbnail, without the output's accumulated damage


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
    def __init__(self, path, per_chat=MEMORY_PER_HISTORY, total=MEMORY_TOTAL,
                 ttl=MEMORY_DAYS * 86400, sample_size=MEMORY_DONORS):
        if min(per_chat, total, ttl, sample_size) <= 0:
            raise ValueError("Memory limits must be positive")
        self.path = str(path)
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        self.per_chat, self.total, self.ttl = per_chat, total, ttl
        self.sample_size = sample_size
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
            # Upgrade existing histories in place; the old effects remain donors.
            columns = {row[1] for row in db.execute("PRAGMA table_info(artifacts)")}
            if "signal" not in columns:
                db.execute("ALTER TABLE artifacts ADD COLUMN signal BLOB NOT NULL DEFAULT X''")
            if "original_image" not in columns:
                db.execute("ALTER TABLE artifacts ADD COLUMN original_image BLOB NOT NULL DEFAULT X''")
            if "created" not in columns:
                db.execute("ALTER TABLE artifacts ADD COLUMN created REAL NOT NULL DEFAULT 0")
                db.execute("UPDATE artifacts SET created=touched")
            if "priority" not in columns:
                db.execute("ALTER TABLE artifacts ADD COLUMN priority REAL NOT NULL DEFAULT 0")
                for (rowid,) in db.execute("SELECT rowid FROM artifacts").fetchall():
                    db.execute("UPDATE artifacts SET priority=? WHERE rowid=?", (random.random(), rowid))
            if "recalled" not in columns:
                db.execute("ALTER TABLE artifacts ADD COLUMN recalled REAL NOT NULL DEFAULT 0")
            db.executescript("""
                CREATE INDEX IF NOT EXISTS artifact_chat_time ON artifacts(chat,touched);
                CREATE INDEX IF NOT EXISTS artifact_age ON artifacts(chat,created);
                CREATE INDEX IF NOT EXISTS artifact_archive ON artifacts(priority);
            """)
            columns = {row[1] for row in db.execute("PRAGMA table_info(preferences)")}
            if "mode" not in columns:
                db.execute("ALTER TABLE preferences ADD COLUMN mode TEXT NOT NULL DEFAULT 'fuse'")
            if "chaos" not in columns:
                db.execute("ALTER TABLE preferences ADD COLUMN chaos INTEGER NOT NULL DEFAULT 0")

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
        self._trim(db, self.total)
        db.execute("DELETE FROM preferences WHERE touched < ?", (time.time() - self.ttl,))
        db.execute("DELETE FROM preferences WHERE rowid NOT IN "
                   "(SELECT rowid FROM preferences ORDER BY touched DESC LIMIT 512)")

    @staticmethod
    def _trim(db, capacity, chat=None):
        """Keep a recent quarter plus a priority reservoir of older uploads."""
        where, args = (" WHERE chat=?", (str(chat),)) if chat is not None else ("", ())
        if db.execute("SELECT count(*) FROM artifacts" + where, args).fetchone()[0] <= capacity:
            return
        recent_count = max(1, capacity // 4)
        recent = "SELECT rowid FROM artifacts" + where + " ORDER BY touched DESC,rowid DESC LIMIT ?"
        kept = {row[0] for row in db.execute(recent, args + (recent_count,))}
        conjunction = " AND " if where else " WHERE "
        archive = ("SELECT rowid FROM artifacts" + where + conjunction + "rowid NOT IN (" + recent + ") "
                   "ORDER BY priority DESC,rowid DESC LIMIT ?")
        kept.update(row[0] for row in db.execute(archive, args + args + (recent_count, capacity - recent_count)))
        removed = [(rowid,) for (rowid,) in db.execute("SELECT rowid FROM artifacts" + where, args)
                   if rowid not in kept]
        db.executemany("DELETE FROM artifacts WHERE rowid=?", removed)

    def _choose(self, rows, now):
        if len(rows) <= self.sample_size:
            chosen = list(rows)
            random.shuffle(chosen)
            return chosen
        quarter = max(1, len(rows) // 4)
        groups = (rows[:quarter], rows[quarter:-quarter], rows[-quarter:])
        counts = (self.sample_size // 4, self.sample_size // 4)
        counts += (max(0, self.sample_size - sum(counts)),)
        chosen = []

        def draw(pool, count):
            pool = list(pool)
            for _ in range(min(count, len(pool))):
                # Recently recalled files get a cooldown, without refreshing expiry.
                weights = [.1 + min(1, max(0, now - row[2]) / 21600) for row in pool]
                row = random.choices(pool, weights=weights)[0]
                chosen.append(row)
                pool.remove(row)

        for group, count in zip(groups, counts):
            draw(group, count)
        ids = {row[0] for row in chosen}
        draw([row for row in rows if row[0] not in ids], self.sample_size - len(chosen))
        random.shuffle(chosen)
        return chosen[:self.sample_size]

    @staticmethod
    def digest(data):
        return hashlib.sha256(data).hexdigest()

    def snapshot(self, chat, digest):
        with self.lock, self._db() as db:
            self._prune(db)
            row = db.execute("SELECT level FROM artifacts WHERE chat=? AND digest=?",
                             (str(chat), digest)).fetchone()
            candidates = db.execute("SELECT digest,created,recalled FROM artifacts WHERE chat=? "
                                    "AND digest<>? ORDER BY created DESC,touched DESC,rowid DESC",
                                    (str(chat), digest)).fetchall()
            if not candidates and row:
                candidates = db.execute("SELECT digest,created,recalled FROM artifacts WHERE chat=? AND digest=?",
                                        (str(chat), digest)).fetchall()
            now = time.time()
            selected = self._choose(candidates, now)
            artifacts = []
            for key, _, _ in selected:
                image, text, sample, signal, original = db.execute(
                    "SELECT image,text,sample,signal,original_image FROM artifacts WHERE chat=? AND digest=?",
                    (str(chat), key)).fetchone()
                artifacts.append(Artifact(image, tuple(json.loads(text)), sample, signal, original))
                db.execute("UPDATE artifacts SET recalled=? WHERE chat=? AND digest=?", (now, str(chat), key))
        return min(12, (row[0] if row else 0) + 1), artifacts

    def remember(self, chat, digest, level, artifact):
        with self.lock, self._db() as db:
            now = time.time()
            db.execute("INSERT INTO artifacts "
                       "(chat,digest,touched,level,image,text,sample,signal,original_image,created,priority) "
                       "VALUES (?,?,?,?,?,?,?,?,?,?,?) ON CONFLICT(chat,digest) DO UPDATE SET "
                       "touched=excluded.touched,level=excluded.level,image=excluded.image,text=excluded.text,"
                       "sample=excluded.sample,signal=excluded.signal,original_image=excluded.original_image",
                       (str(chat), digest, now, level, artifact.image,
                        json.dumps(artifact.text[:5], ensure_ascii=False), artifact.sample[:4096],
                        artifact.signal[:4096], artifact.original_image, now, random.random()))
            self._trim(db, self.per_chat, chat)
            self._prune(db)

    def translations(self, chat, enabled=None):
        with self.lock, self._db() as db:
            self._prune(db)
            if enabled is not None:
                db.execute("INSERT INTO preferences (chat,translate_enabled,touched) VALUES (?,?,?) "
                           "ON CONFLICT(chat) DO UPDATE SET translate_enabled=excluded.translate_enabled, "
                           "touched=excluded.touched",
                           (str(chat), int(enabled), time.time()))
            row = db.execute("SELECT translate_enabled FROM preferences WHERE chat=?", (str(chat),)).fetchone()
        return bool(row and row[0])

    def settings(self, chat, mode=None, chaos=None):
        if mode is not None and mode not in ("fuse", "raw"):
            raise ValueError("Mode must be fuse or raw")
        if chaos is not None and (not isinstance(chaos, int) or not 0 <= chaos <= 12):
            raise ValueError("Chaos must be 0 (auto) or 1–12")
        with self.lock, self._db() as db:
            self._prune(db)
            if mode is not None or chaos is not None:
                db.execute("INSERT INTO preferences (chat,translate_enabled,touched) VALUES (?,0,?) "
                           "ON CONFLICT(chat) DO UPDATE SET touched=excluded.touched",
                           (str(chat), time.time()))
                if mode is not None:
                    db.execute("UPDATE preferences SET mode=? WHERE chat=?", (mode, str(chat)))
                if chaos is not None:
                    db.execute("UPDATE preferences SET chaos=? WHERE chat=?", (chaos, str(chat)))
            row = db.execute("SELECT mode,chaos FROM preferences WHERE chat=?", (str(chat),)).fetchone()
        return tuple(row) if row else ("fuse", 0)

    def forget(self, chat):
        with self.lock, self._db() as db:
            db.execute("DELETE FROM artifacts WHERE chat=?", (str(chat),))
            db.execute("DELETE FROM preferences WHERE chat=?", (str(chat),))
