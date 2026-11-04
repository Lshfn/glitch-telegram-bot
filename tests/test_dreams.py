import asyncio
import random
import tempfile
import time
import unittest
import wave
from concurrent.futures import ThreadPoolExecutor
from io import BytesIO
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import jwt
import libsql
import numpy as np
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer
from cryptography.hazmat.primitives.asymmetric import rsa
from PIL import Image

from artifact_memory import Artifact, ArtifactMemory
from content_fusion import project
from dream_auth import ISSUER, REPOSITORY, WORKFLOW_REF, authorize_dream_tick
from memory_dreams import make_dream
from test_engine import png
import test_memory_limits as memory_test_helpers
import glitch_bot as engine


def donors():
    return [Artifact(original_image=png("red"), text=("старые слова одного файла",), signal=project(np.arange(50))),
            Artifact(original_image=png("blue"), text=("новые слова другого файла",), signal=project(np.sin(np.arange(50))))]


class DreamScheduleTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / "history.db"
        self.memory = ArtifactMemory(self.path)
        for i, artifact in enumerate(donors()):
            self.memory.remember("1:1", str(i), 1, artifact)

    def due(self, memory=None):
        memory = memory or self.memory
        memory.schedule_dream("1:1")
        with memory._db() as db:
            db.execute("UPDATE memory_dreams SET next_at=0")

    def test_random_schedule_survives_reopen_and_upload_does_not_reset_due_or_opt_out(self):
        with patch("artifact_memory.time.time", return_value=1800000000):
            self.memory.schedule_dream("1:1")
            with self.memory._db() as db:
                first = db.execute("SELECT next_at FROM memory_dreams").fetchone()[0]
            self.assertGreaterEqual(first, 1800000000 + 6 * 3600)
            self.assertLessEqual(first, 1800000000 + 30 * 3600)
            reopened = ArtifactMemory(self.path)
            reopened.schedule_dream("1:1")
            with reopened._db() as db:
                self.assertEqual(db.execute("SELECT next_at FROM memory_dreams").fetchone()[0], first)
            reopened.schedule_dream("1:1", False)
            reopened.schedule_dream("1:1")
            with reopened._db() as db:
                self.assertEqual(db.execute("SELECT enabled FROM memory_dreams").fetchone()[0], 0)

    def test_restart_or_overlapping_workers_claim_once_without_catchup_bursts(self):
        self.due()
        workers = [ArtifactMemory(self.path), ArtifactMemory(self.path)]
        with ThreadPoolExecutor(2) as pool:
            results = list(pool.map(lambda memory: memory.claim_dreams(), workers))
        self.assertEqual(sum(map(len, results)), 1)
        self.assertEqual(ArtifactMemory(self.path).claim_dreams(), [])
        scope, token = next(value[0] for value in results if value)
        self.assertTrue(self.memory.dream_allowed(scope, token))
        self.memory.finish_dream(scope, token, delivered=True)
        self.assertFalse(self.memory.dream_allowed(scope, token))

    def test_forget_opt_out_groups_empty_histories_and_single_file_do_not_send(self):
        for scope in ("-100:1", "1:2", "0:0", "group", "1:1:1"):
            with self.assertRaises(ValueError):
                self.memory.schedule_dream(scope)
        self.memory.schedule_dream("2:2")
        self.memory.remember("2:2", "only", 1, donors()[0])
        self.due()
        claims = self.memory.claim_dreams()
        self.assertEqual(len(claims), 1)
        self.assertEqual(claims[0][0], "1:1")
        self.memory.forget("1:1")
        self.assertFalse(self.memory.dream_allowed(*claims[0]))
        self.assertEqual(self.memory.claim_dreams(), [])
        self.due()
        self.memory.schedule_dream("1:1", False)
        self.assertEqual(self.memory.claim_dreams(), [])

    def test_blocked_chat_disabled_and_retry_after_respected(self):
        self.due()
        claim = self.memory.claim_dreams()[0]
        self.memory.finish_dream(*claim, blocked=True)
        with self.memory._db() as db:
            self.assertEqual(db.execute("SELECT enabled FROM memory_dreams").fetchone()[0], 0)
        self.due()
        self.memory.schedule_dream("1:1", True)
        with self.memory._db() as db:
            db.execute("UPDATE memory_dreams SET next_at=0")
        claim = self.memory.claim_dreams()[0]
        self.memory.finish_dream(*claim, retry_after=1000000)
        with self.memory._db() as db:
            self.assertGreater(db.execute("SELECT next_at FROM memory_dreams").fetchone()[0], time.time() + 999990)

    def test_same_reservation_and_deletion_queries_work_through_real_libsql_driver(self):
        connect = libsql.connect
        with patch("libsql.connect", side_effect=lambda *args, **kwargs: connect(str(self.path))):
            cloud = ArtifactMemory("unused", remote_url="libsql://test.turso.io", auth_token="test-only")
            self.due(cloud)
            claim = cloud.claim_dreams()[0]
            self.assertTrue(cloud.dream_allowed(*claim))
            cloud.forget("1:1")
            self.assertFalse(cloud.dream_allowed(*claim))


class DreamFileTests(unittest.TestCase):
    def test_mixed_outputs_decode_and_audio_is_bounded_with_fades(self):
        for kind in ("png", "pdf", "txt", "wav"):
            with self.subTest(kind=kind):
                payload, name = make_dream(donors(), kind)
                self.assertTrue(name.endswith("." + kind))
                self.assertLess(len(payload), 2_000_000)
                if kind == "png":
                    with Image.open(BytesIO(payload)) as image:
                        image.load()
                        self.assertLessEqual(max(image.size), 640)
                elif kind == "pdf":
                    self.assertTrue(payload.startswith(b"%PDF"))
                elif kind == "txt":
                    text = payload.decode("utf-8")
                    self.assertIn("старые", text)
                    self.assertIn("новые", text)
                else:
                    with wave.open(BytesIO(payload)) as audio:
                        self.assertEqual(audio.getnchannels(), 2)
                        self.assertTrue(6 <= audio.getnframes() / audio.getframerate() <= 12)
                        samples = np.frombuffer(audio.readframes(audio.getnframes()), dtype="<i2")
                    self.assertLessEqual(abs(samples.astype(int)).max(), 21300)
                    self.assertEqual(tuple(samples[:2]), (0, 0))
                    self.assertEqual(tuple(samples[-2:]), (0, 0))

    def test_generated_files_vary_and_use_changed_donor_material(self):
        rendered = set()
        for seed in range(6):
            random.seed(seed)
            rendered.add(make_dream(donors(), "png")[0])
        self.assertGreater(len(rendered), 3)
        random.seed(3)
        before = make_dream(donors(), "png")[0]
        altered = donors()
        altered[1] = Artifact(original_image=png("lime"), signal=project(np.cos(np.arange(50))))
        random.seed(3)
        self.assertNotEqual(before, make_dream(altered, "png")[0])
        with self.assertRaises(ValueError):
            make_dream(donors()[:1])


class DreamDeliveryTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.memory = ArtifactMemory(Path(self.temp.name) / "memory.db")
        for i, artifact in enumerate(donors()):
            self.memory.remember("1:1", str(i), 1, artifact)
        self.memory.schedule_dream("1:1")
        with self.memory._db() as db:
            db.execute("UPDATE memory_dreams SET next_at=0")

    async def test_send_has_no_caption_or_feedback_and_is_not_repeated(self):
        bot = SimpleNamespace(send_document=AsyncMock())
        with patch.object(engine, "memory", self.memory), patch.object(engine, "bot", bot), patch.object(engine, "MEMORY_DREAMS", True):
            self.assertEqual(await engine.send_due_dreams(), 1)
            self.assertEqual(await engine.send_due_dreams(), 0)
        bot.send_document.assert_awaited_once()
        self.assertEqual(bot.send_document.call_args.kwargs["chat_id"], 1)
        self.assertNotIn("caption", bot.send_document.call_args.kwargs)
        self.assertEqual(len(self.memory.snapshot("1:1", "")[1]), 2)

    async def test_opt_out_during_render_and_delivery_failure_do_not_replay(self):
        bot = SimpleNamespace(send_document=AsyncMock())
        def cancel_dream(artifacts):
            self.memory.schedule_dream("1:1", False)
            return b"x", "memory.txt"
        with patch.object(engine, "memory", self.memory), patch.object(engine, "bot", bot), patch.object(engine, "MEMORY_DREAMS", True), patch.object(engine, "make_dream", side_effect=cancel_dream):
            self.assertEqual(await engine.send_due_dreams(), 0)
        bot.send_document.assert_not_awaited()
        self.memory.schedule_dream("1:1", True)
        with self.memory._db() as db:
            db.execute("UPDATE memory_dreams SET next_at=0")
        bot.send_document.side_effect = RuntimeError("network unavailable")
        with patch.object(engine, "memory", self.memory), patch.object(engine, "bot", bot), patch.object(engine, "MEMORY_DREAMS", True):
            self.assertEqual(await engine.send_due_dreams(), 0)
            self.assertEqual(await engine.send_due_dreams(), 0)
        bot.send_document.assert_awaited_once()

    async def test_new_private_upload_enrolls_and_off_command_is_preserved(self):
        message = memory_test_helpers.LargerInputTests.message(SimpleNamespace(file_size=10, file_id="upload", file_name="file.txt"))
        message.chat = SimpleNamespace(id=1, type="private")
        message.from_user = SimpleNamespace(id=1)
        bot = SimpleNamespace(get_file=AsyncMock(return_value=SimpleNamespace(file_path="file.txt")),
                              download_file=AsyncMock(side_effect=lambda *a, **kw: BytesIO(b"some text")))
        processor = AsyncMock(return_value=(b"out", [], donors()[0], [], "file.txt"))
        with patch.object(engine, "memory", self.memory), patch.object(engine, "bot", bot), patch.object(engine, "MEMORY_DREAMS", True), patch.object(engine, "process_upload", processor):
            message.text = "/dreams off"
            await engine.handle_file(message)
            message.text = None
            await engine.handle_file(message)
        with self.memory._db() as db:
            self.assertEqual(db.execute("SELECT enabled FROM memory_dreams WHERE chat='1:1'").fetchone()[0], 0)
            db.execute("DELETE FROM memory_dreams WHERE chat='1:1'")
        with patch.object(engine, "memory", self.memory), patch.object(engine, "bot", bot), patch.object(engine, "MEMORY_DREAMS", True), patch.object(engine, "process_upload", processor):
            await engine.handle_file(message)
        with self.memory._db() as db:
            self.assertEqual(db.execute("SELECT enabled FROM memory_dreams WHERE chat='1:1'").fetchone()[0], 1)


class WorkflowAuthTests(unittest.IsolatedAsyncioTestCase):
    @classmethod
    def setUpClass(cls):
        cls.key = rsa.generate_private_key(public_exponent=65537, key_size=2048)

    def token(self, **changes):
        now = time.time()
        claims = dict(iss=ISSUER, aud="https://bot.example", sub="workflow", iat=now, nbf=now-1, exp=now+120,
                      repository=REPOSITORY, repository_id="1170794745", repository_owner_id="93941329",
                      ref="refs/heads/main", workflow_ref=WORKFLOW_REF, event_name="schedule")
        claims.update(changes)
        return jwt.encode(claims, self.key, algorithm="RS256", headers={"kid": "test"})

    async def test_signed_workflow_only_wrong_claims_expiry_and_signature_rejected(self):
        with patch("dream_auth.KEYS.get_signing_key_from_jwt", return_value=SimpleNamespace(key=self.key.public_key())):
            self.assertTrue(authorize_dream_tick("Bearer " + self.token(), "https://bot.example"))
            for changes in ({"repository": "someone/fork"}, {"repository_id": "0"}, {"repository_owner_id": "0"},
                            {"ref": "refs/pull/1/merge"}, {"workflow_ref": "other.yml"}, {"event_name": "pull_request"},
                            {"aud": "https://attacker.example"}, {"iss": "https://attacker.example"}, {"exp": 1}):
                with self.subTest(changes=changes), self.assertRaises(jwt.InvalidTokenError):
                    authorize_dream_tick("Bearer " + self.token(**changes), "https://bot.example")
            with self.assertRaises(jwt.InvalidTokenError):
                authorize_dream_tick("Bearer " + jwt.encode({"exp": time.time()+60}, "x" * 32, algorithm="HS256"), "https://bot.example")
            other = rsa.generate_private_key(public_exponent=65537, key_size=2048)
            with patch("dream_auth.KEYS.get_signing_key_from_jwt", return_value=SimpleNamespace(key=other.public_key())):
                with self.assertRaises(jwt.InvalidSignatureError):
                    authorize_dream_tick("Bearer " + self.token(), "https://bot.example")

    async def test_endpoint_rejects_anonymous_request_before_any_delivery(self):
        app = web.Application()
        app.router.add_post("/memory-tick", engine.memory_tick)
        async with TestClient(TestServer(app)) as client:
            with patch.object(engine, "send_due_dreams", new_callable=AsyncMock) as send, patch.dict("os.environ", RENDER_EXTERNAL_URL="https://bot.example"):
                response = await client.post("/memory-tick")
                self.assertEqual(response.status, 403)
                send.assert_not_awaited()
                with patch("dream_auth.KEYS.get_signing_key_from_jwt", return_value=SimpleNamespace(key=self.key.public_key())):
                    response = await client.post("/memory-tick", headers={"Authorization": "Bearer " + self.token()})
                self.assertEqual(response.status, 200)
                send.assert_awaited_once()

    async def test_background_task_is_cancelled_on_shutdown(self):
        cancelled = asyncio.Event()
        async def loop():
            try:
                await asyncio.Future()
            finally:
                cancelled.set()
        with patch.object(engine, "dream_loop", loop):
            lifecycle = engine.dream_lifecycle(None)
            await anext(lifecycle)
            await asyncio.sleep(0)
            with self.assertRaises(StopAsyncIteration):
                await anext(lifecycle)
        self.assertTrue(cancelled.is_set())
