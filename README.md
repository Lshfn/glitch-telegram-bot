# Glitch Telegram bot

The bot makes images inherit crops, scan strips, ghost layers, channel fragments
and snippets from previous uploads. Text documents inherit phrases; other files
inherit payload bytes. Each render still uses a random recipe and order of
effects. Reuploading the same content increases intensity, even if Telegram gives
it a different file ID. Unknown files and video/GIF byte glitches are deliberately
destructive and are not guaranteed to remain playable.

## Memory

Histories belong to **one sender in one chat**. Nothing crosses between users or
chats. The bot remembers traces of up to 24 distinct recent uploads per history,
256 uploads in total, for seven days; repeated content updates its existing entry.
Only a small re-encoded thumbnail, up to five text excerpts and up to 4 KB of
payload fragments are retained. Originals and filenames are not stored. Effects
from earlier outputs can recur and drift through later ones.

`/forget` clears that history, intensity and translation preference. `/status`
shows memory and translation/OCR availability. `/help` explains the commands.
Storage is SQLite at `data/artifacts.sqlite3` (override `MEMORY_DB`). Place it on
a persistent volume to retain memory across deploys. Render's free ephemeral
filesystem loses memory on restart/redeploy; a paid disk is **not** provisioned
by this project. Seven-day expiry is applied on access, not by a background task.

## Random translations

Set `TRANSLATE_URL` to the root URL of a LibreTranslate-compatible service, e.g.
`https://your-translator.example`, with **no** `/translate` suffix. Set
`TRANSLATE_API_KEY` if it requires authentication. No public service or API key is
silently selected. You can self-host LibreTranslate; see
<https://docs.libretranslate.com/guides/installation/>.

Then send `/translate on` to the bot. This opts that sender/chat into sending
selected snippets to the configured provider; images are OCR'd locally and only
recognized text is sent, never the image itself. `/translate off` stops this.
Translation is off by default. Without a service, memory effects still work and
the command explains the missing configuration. Group members cannot enable
translation for each other's uploads.

Each eligible upload selects a few phrases/lines and random target languages.
Text regions in images are painted over with translated text, fitted into their
original boxes, before the image glitches. Plain `.txt`, `.md`, `.log`, `.rst`,
`.srt` and `.vtt` files are decoded as UTF-8, BOM-marked UTF-16, or Windows-1251
and returned as UTF-8. These are artistic transformations: Markdown/subtitle
structure need not survive. PDFs, DOCX and structured/source formats do not have
semantic text replacement; they keep the binary-glitch behavior.

`TRANSLATE_TARGETS` defaults to `en,ru,de,fr,es,it,pt,el,uk,pl,tr`; set it to
languages supported by your provider. `OCR_LANGUAGES` defaults to `eng+rus`;
only installed Tesseract packs are used. OCR can miss handwriting, stylized,
small or rotated text. A flat local background approximates erased text, rather
than reconstructing the original scene. Unsupported fonts/languages can require
installing a suitable font and setting `GLITCH_FONT` to its path. The Docker image
includes Noto Sans for the default target scripts. Provider failures/timeouts are
reported in the output caption and do not stop ordinary glitches.

## Run and deploy

For local Python: `pip install -r requirements.txt`, install Tesseract plus the
English/Russian language packs and a suitable font, then set `BOT_TOKEN` and run
`python glitch_bot.py`. Polling mode removes an existing webhook for this token:
use a separate test bot when trying the branch locally.

The Dockerfile includes OCR and fonts. Build with
`docker build -t glitch-bot .`, then run with
`docker run --env-file .env -v glitch-memory:/app/data glitch-bot`.
Keep `.env` out of git. Add `BOT_TOKEN`, `TRANSLATE_URL`, and any optional
`TRANSLATE_API_KEY` there. Uploads are limited to 10 MB, text files to 512 KB,
images to 12 megapixels and processing resolution to 1600 px on the longest edge.
Heavy processing runs off the event loop; at most two uploads run concurrently,
with ordered processing within each sender/chat history.

### Existing Render service

This branch changes the blueprint to Docker so OCR is actually installed.
Changing `render.yaml` alone may not convert an existing native-Python service.
Deploy the branch as a **Docker** web service (or update the service runtime if
Render offers that option), preserve `BOT_TOKEN`, and set `TRANSLATE_URL` and
optional `TRANSLATE_API_KEY` in its environment. Retain the `/health` endpoint and
automatic `RENDER_EXTERNAL_URL`. Stop the old service before activating another
service with the same token, so they do not compete over the Telegram webhook.
No hosted service has been reconfigured by this code change.

## Verify

`python -m unittest discover -s tests -v`

Tests cover memory isolation/expiry/bounds/restarts, inheritance across formats,
Unicode text, valid image outputs, translation HTTP requests and failures, and
real local OCR when Tesseract is installed. They use a local fake translation
server and no real Telegram account or third-party user content. Provider
translation quality and the deployed Telegram/Render environment still need a
test upload after configuration.
