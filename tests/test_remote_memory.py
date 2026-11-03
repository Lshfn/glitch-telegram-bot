"""Exercise the real libSQL driver locally; cloud credentials are never needed."""
import os
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import libsql

from artifact_memory import Artifact, ArtifactMemory, configured_memory


class RemoteMemoryTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)
        self.cloud = self.root / "cloud.sqlite3"
        self.local = self.root / "unused" / "local.sqlite3"
        self.connect = libsql.connect

        def local_driver(database, *, auth_token, timeout):
            self.assertEqual(database, "libsql://memory.example.turso.io")
            self.assertEqual(auth_token, "test-only-token")
            return self.connect(str(self.cloud), timeout=timeout)

        self.connection_patch = patch("libsql.connect", side_effect=local_driver)
        self.connection_patch.start()
        self.addCleanup(self.connection_patch.stop)

    def memory(self, **options):
        return ArtifactMemory(self.local, remote_url="libsql://memory.example.turso.io",
                              auth_token="test-only-token", **options)

    def test_driver_migrates_old_schema_and_restores_blobs_preferences_and_forget(self):
        with sqlite3.connect(self.cloud) as db:
            db.executescript("""
                CREATE TABLE artifacts(chat TEXT, digest TEXT, touched REAL, level INTEGER,
                    image BLOB, text TEXT, sample BLOB, PRIMARY KEY(chat,digest));
                CREATE TABLE preferences(chat TEXT PRIMARY KEY, translate_enabled INTEGER,
                    touched REAL);
            """)
            db.execute("INSERT INTO artifacts VALUES(?,?,?,2,?,?,?)",
                       ("a", "legacy", 1800000000, b"old-image", '["old words"]', b"old-sample"))
        with patch("artifact_memory.time.time", return_value=1800000001):
            memory = self.memory()
            self.assertEqual(memory.snapshot("a", "")[1][0].text, ("old words",))
            artifact = Artifact(b"image\x00", ("слова",), b"sample", b"signal", b"original")
            memory.remember("a", "new", 5, artifact)
            memory.translations("a", True)
            memory.settings("a", mode="raw", chaos=3)
            reopened = self.memory()
            self.assertIn(artifact, reopened.snapshot("a", "")[1])
            self.assertTrue(reopened.translations("a"))
            self.assertEqual(reopened.settings("a"), ("raw", 3))
            self.assertEqual(reopened.snapshot("another-user", ""), (1, []))
            reopened.forget("a")
            self.assertEqual(reopened.snapshot("a", ""), (1, []))
            self.assertFalse(reopened.translations("a"))
        self.assertFalse(self.local.parent.exists())

    def test_driver_enforces_archive_limits_repeat_age_and_expiry(self):
        memory = self.memory(per_chat=4, total=6)
        for i in range(10):
            with patch("artifact_memory.time.time", return_value=1800000000 + i):
                memory.remember("a", str(i), 1, Artifact(text=(str(i),)))
        with patch("artifact_memory.time.time", return_value=1800000020):
            self.assertEqual(len(memory.snapshot("a", "")[1]), 4)
            with memory._db() as db:
                age = db.execute("SELECT created,priority FROM artifacts WHERE digest='9'").fetchone()
            memory.remember("a", "9", 6, Artifact(text=("changed",)))
            with memory._db() as db:
                self.assertEqual(db.execute("SELECT created,priority FROM artifacts WHERE digest='9'").fetchone(), age)
            for i in range(5):
                memory.remember("b", str(i), 1, Artifact())
            with memory._db() as db:
                self.assertLessEqual(db.execute("SELECT count(*) FROM artifacts").fetchone()[0], 6)
        with patch("artifact_memory.time.time", return_value=1800000000 + 181 * 86400):
            self.assertEqual(memory.snapshot("a", "")[1], [])
            self.assertEqual(memory.snapshot("b", "")[1], [])

    def test_remote_transaction_rolls_back_and_connection_failure_never_falls_back_to_disk(self):
        memory = self.memory()
        with self.assertRaisesRegex(ValueError, "rollback"):
            with memory._db() as db:
                db.execute("INSERT INTO preferences(chat,translate_enabled,touched) VALUES('a',1,0)")
                raise ValueError("rollback")
        with memory._db() as db:
            self.assertEqual(db.execute("SELECT count(*) FROM preferences").fetchone()[0], 0)
        with patch("libsql.connect", side_effect=libsql.Error("offline")), patch("sqlite3.connect") as local:
            with self.assertRaises(libsql.Error):
                memory.remember("a", "lost", 1, Artifact())
            local.assert_not_called()
        self.assertFalse(self.local.parent.exists())

    def test_only_complete_explicit_owner_configuration_enables_remote_memory(self):
        base = {"MEMORY_DB": str(self.local)}
        with patch.dict(os.environ, base, clear=True):
            self.assertFalse(configured_memory().remote_url)
        for extra in ({"TURSO_DATABASE_URL": "libsql://memory.example.turso.io"},
                      {"TURSO_AUTH_TOKEN": "test-only-token"}):
            with patch.dict(os.environ, dict(base, **extra), clear=True):
                with self.assertRaisesRegex(ValueError, "requires both"):
                    configured_memory()
        with patch.dict(os.environ, dict(base, TURSO_DATABASE_URL="libsql://memory.example.turso.io",
                                        TURSO_AUTH_TOKEN="test-only-token"), clear=True):
            self.assertTrue(configured_memory().remote_url)
        for url in ("http://memory.example", "libsql://user:secret@host", "libsql://host?token=secret"):
            with self.assertRaises(ValueError) as error:
                ArtifactMemory(self.local, remote_url=url, auth_token="secret")
            self.assertNotIn("secret", str(error.exception))


class WorkerIsolationTests(unittest.IsolatedAsyncioTestCase):
    async def test_content_worker_does_not_inherit_cloud_credentials(self):
        from test_content import wav_bytes
        from file_formats import run_content_job
        import asyncio

        with patch.dict(os.environ, TURSO_DATABASE_URL="libsql://never-contact.invalid",
                        TURSO_AUTH_TOKEN="test-only-token"):
            output, _, _, _, extension = await asyncio.to_thread(run_content_job, wav_bytes(.1), ".wav", 1, [])
        self.assertEqual(extension, ".wav")
        self.assertTrue(output.startswith(b"RIFF"))
