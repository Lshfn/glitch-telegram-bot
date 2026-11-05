"""MyMemory's HTTP contract, quota handling and failure-safe replacements."""
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from aiohttp import ClientSession, web

from language_glitch import Translator


class MyMemoryTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.requests = []
        self.reply = {"responseStatus": 200, "quotaFinished": False,
                      "responseData": {"translatedText": "l&#39;image garde la mémoire",
                                       "detectedLanguage": "en"}}

        async def translate(request):
            self.requests.append(dict(request.query))
            return web.json_response(self.reply)

        app = web.Application()
        app.router.add_get("/get", translate)
        self.runner = web.AppRunner(app)
        await self.runner.setup()
        site = web.TCPSite(self.runner, "127.0.0.1", 0)
        await site.start()
        port = site._server.sockets[0].getsockname()[1]
        self.endpoint = f"http://127.0.0.1:{port}/get"
        self.translator = Translator(provider="mymemory", targets=["fr"])
        self.session = ClientSession()
        original_get = self.session.get

        def local_get(url, **kwargs):
            self.assertEqual(url, "https://api.mymemory.translated.net/get")
            self.assertFalse(kwargs["allow_redirects"])
            return original_get(self.endpoint, **kwargs)

        self.redirect = patch.object(self.session, "get", side_effect=local_get)
        self.redirect.start()

    async def asyncTearDown(self):
        self.redirect.stop()
        await self.session.close()
        await self.runner.cleanup()

    async def test_keyless_translation_autodetects_source_and_decodes_entities(self):
        result, targets, failures = await self.translator.replace_spans(
            "The image remembers.", self.session)
        self.assertTrue(self.translator.configured)
        self.assertEqual(result, "l'image garde la mémoire")
        self.assertEqual(targets, ["fr"])
        self.assertEqual(failures, 0)
        self.assertEqual(self.requests, [{"q": "The image remembers.",
                                          "langpair": "autodetect|fr"}])

    async def test_multibyte_request_stays_within_provider_byte_limit(self):
        await self.translator.translate(self.session, "記憶" * 120)
        query = self.requests[0]["q"]
        self.assertLessEqual(len(query.encode("utf-8")), 500)
        self.assertTrue(query)
        self.assertNotIn("\ufffd", query)

    async def test_quota_message_never_replaces_text_and_pauses_requests(self):
        self.reply = {"responseStatus": 429, "quotaFinished": True,
                      "responseData": {"translatedText": "DAILY LIMIT REACHED"}}
        original = "The first phrase.\nAnother memory."
        result, targets, failures = await self.translator.replace_spans(original, self.session)
        self.assertEqual(result, original)
        self.assertEqual(targets, [])
        self.assertEqual(failures, 2)
        self.assertEqual(len(self.requests), 1)
        # The cooldown expires without a restart or changing the user's opt-in.
        self.reply = {"responseStatus": 200,
                      "responseData": {"translatedText": "un souvenir"}}
        with patch("language_glitch.time.monotonic", return_value=self.translator.retry_at + 1):
            value, target = await self.translator.translate(self.session, "A memory.")
        self.assertEqual((value, target), ("un souvenir", "fr"))

    async def test_provider_error_and_malformed_reply_keep_original(self):
        for reply in ([], {"responseStatus": 403, "responseData": {"translatedText": "ERROR"}},
                      {"responseStatus": 200, "responseData": []},
                      {"responseStatus": 200, "responseData": {"translatedText": ""}}):
            with self.subTest(reply=reply):
                self.reply = reply
                result, targets, failures = await self.translator.replace_spans(
                    "The image remembers.", self.session)
                self.assertEqual((result, targets, failures), ("The image remembers.", [], 1))

    async def test_detected_source_is_not_counted_as_translation(self):
        self.reply["responseData"]["detectedLanguage"] = "fr"
        value, target = await self.translator.translate(self.session, "Une image.")
        self.assertEqual((value, target), ("Une image.", None))

    async def test_time_budget_preserves_already_translated_spans(self):
        # Advancing only this module's clock leaves the event loop clock intact.
        clock = SimpleNamespace(monotonic=lambda: 23 if self.requests else 0)
        with patch("language_glitch.time", clock):
            result, targets, failures = await self.translator.replace_spans(
                "The first phrase.\nThe last phrase.", self.session)
        self.assertIn("The first phrase.", result)
        self.assertIn("l'image garde la mémoire", result)
        self.assertEqual(targets, ["fr"])
        self.assertEqual(failures, 1)
        self.assertEqual(len(self.requests), 1)


if __name__ == "__main__":
    unittest.main()
