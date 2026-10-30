# Glitch Telegram bot

A poetic glitch interpretation of what is happening to Telegram in Russia.
The bot turns interruptions in communication into changes in the files themselves:
images break apart, fragments of earlier uploads return, and words slip into other languages.

![A glitched image produced by the bot](<glitched_image%20(31).jpg>)

## How it works

The bot makes images inherit crops, scan strips, ghost layers, channel fragments
and snippets from previous uploads. Documents, sound and moving images now take
part in the same memory. A paragraph can become a displacement field for an
image; an audio waveform can become a document's word-order pattern; video frames
can return inside later uploads.

The original eleven image effects, JPEG databend, phrase inheritance, translations
and byte chaos remain. Each render chooses a different recipe and order.
Reuploading the same content increases intensity, even if Telegram gives it a
different file ID. Use `/chaos 12` to reach maximum intensity immediately;
`/chaos auto` restores escalation on repeated content.

## Content fusion

`/mode fuse` is the default. Transformations work on decoded file content and
rebuild the file in the format below. Ordinary Telegram audio, voice messages,
animations and video notes are accepted as well as file attachments.

| Input | Material used | Output |
| --- | --- | --- |
| JPEG, PNG, WebP, BMP, TIFF | Pixels, file-byte projection, previous images/text/signals | JPEG for JPEG inputs; PNG otherwise |
| DOCX | Text runs in the body, tables, headers, footers, footnotes/endnotes; up to 12 embedded raster images | DOCX with run styling and package relationships retained |
| PDF | Every page, extracted text, page contact sheet, file-byte projection | Raster PDF with the same page count; selectable text, links and forms are flattened |
| WAV, MP3, OGG/Opus, FLAC, M4A, AAC, AIFF | Decoded waveform, waveform drawing, file-byte projection | Stereo 22.05 kHz, 16-bit PCM WAV |
| MP4, MOV, MKV, WebM, AVI, GIF | Decoded frames, contact sheet, any audio, temporal feedback | H.264 MP4 at 8 fps, up to 480 px; audio becomes AAC |
| Other files | Payload bytes and earlier content projections | Same extension; intentionally destructive |

The additional mathematical variations include:

- Fourier magnitude/phase crossover between the source and donor signals.
- Displacement and modular coordinate maps driven by decoded material.
- Gray–Scott reaction–diffusion initialized with source pixels and donor content.
- Bitplane weaving between images, waveforms and file-derived fields.
- Audio spectral vocoding, content-derived convolution kernels, grain
  permutations, phase modulation and wavefolding.
- Logistic-map word permutations using the source and earlier uploads.
- Video frame permutations and delayed-frame blend, scan and difference feedback.
- Finite-field multiplication, convolution modulo 256 and modular weaving for
  generic payload bytes.

Randomness selects the recipe. The uploaded material supplies the numerical
fields, phases, kernels and words. This also works on the first upload, using
its own content; later uploads contribute additional material. Image mathematics
uses a working grid up to 768 px and returns to the image's processing dimensions.

`/mode raw` restores the earlier processing route: existing image/text effects
and inherited byte fragments followed by byte chaos for documents, audio, video,
GIF and unknown files. Those binary outputs may be unreadable. In content mode,
an unavailable/failed decoder also uses this destructive fallback, with an
explicit caption. A processing-limit overrun is reported without returning a
truncated or silently corrupted substitute.

The mode and chaos settings apply to one sender in one chat, survive restarts
when the database survives, and reset with `/forget`.

## Memory

Histories belong to **one sender in one chat**. Nothing crosses between users or
chats. The bot remembers traces of up to 24 distinct recent uploads per history,
256 uploads in total, for seven days; repeated content updates its existing entry.
Only a small re-encoded thumbnail/contact sheet, up to five text excerpts, up to
4 KB of payload fragments and up to 4 KB of projected content signals are
retained. These traces combine original and transformed material. Complete files
and filenames are not stored in the history. Temporary decoder files are deleted
after processing. Effects from earlier outputs can recur and drift through later
ones. Existing SQLite histories migrate automatically and retain their old traces.

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
structure need not survive. Random translation still applies to images and
these plain-text formats. DOCX/PDF content fusion and word permutations are local
and do not send extracted document text to the translation provider.

`TRANSLATE_TARGETS` defaults to `en,ru,de,fr,es,it,pt,el,uk,pl,tr`; set it to
languages supported by your provider. `OCR_LANGUAGES` defaults to `eng+rus`;
only installed Tesseract packs are used. OCR can miss handwriting, stylized,
small or rotated text. A flat local background approximates erased text, rather
than reconstructing the original scene. Unsupported fonts/languages can require
installing a suitable font and setting `GLITCH_FONT` to its path. The Docker image
includes Noto Sans for the default target scripts. Provider failures/timeouts are
reported in the output caption and do not stop ordinary glitches.

## Run and deploy

For local Python on Linux: `pip install -r requirements.txt`, install FFmpeg,
Poppler utilities, Tesseract plus the English/Russian language packs and a
suitable font, then set `BOT_TOKEN` and run
`python glitch_bot.py`. Polling mode removes an existing webhook for this token:
use a separate test bot when trying the branch locally.

The Dockerfile includes OCR, fonts, FFmpeg and Poppler. Build with
`docker build -t glitch-bot .`, then run with
`docker run --env-file .env -v glitch-memory:/app/data glitch-bot`.
Keep `.env` out of git. Add `BOT_TOKEN`, `TRANSLATE_URL`, and any optional
`TRANSLATE_API_KEY` there. Uploads are limited to 10 MB, text files to 512 KB,
images to 12 megapixels and processing resolution to 1600 px on the longest edge.
Heavy processing runs off the event loop; at most two uploads run concurrently,
with ordered processing within each sender/chat history. Only one document/media
worker runs at a time. Workers have a 60-second wall deadline, a 45-second CPU
budget and a 768 MB address-space limit; their decoder subprocesses inherit the
limits and are killed with the worker on timeout. Use Docker on other platforms.

Content-mode limits are 12 PDF pages, 60 seconds of audio, 12 seconds of video,
32 MB of expanded DOCX data, 128K DOCX text characters, 4000 DOCX paragraphs,
16K characters per DOCX paragraph, 256 KB of extracted PDF text and 16 MB per
output. Video input frames may contain at most 12 megapixels. Longer/larger files
must be shortened/split first or sent in `/mode raw`. Limits bound processing;
chaos intensity controls the transformations within them.

### Existing Render service

Deploy the changes to the existing **Docker** web service so the new FFmpeg and
Poppler packages are installed. Changing `render.yaml` alone may not convert an
existing native-Python service. Use a Docker service (or update the runtime if
Render offers that option), preserve `BOT_TOKEN`, and set `TRANSLATE_URL` and
optional `TRANSLATE_API_KEY` in its environment. Retain the `/health` endpoint and
automatic `RENDER_EXTERNAL_URL`. Stop the old service before activating another
service with the same token, so they do not compete over the Telegram webhook.
No hosted service has been reconfigured by this code change.

## Verify

Install test dependencies with `pip install -r requirements-dev.txt`, then run
`python -m unittest discover -s tests -v`. FFmpeg and Poppler are needed for the
media/PDF integration tests; CI installs them alongside OCR.

Tests cover memory isolation/expiry/bounds/restarts and schema migration, original
and transformed traces, every mathematical image/audio variation, Unicode text,
reopened DOCX/PDF/WAV files, MP4 video and audio decoding, resource limits, the
destructive fallback, voice-message routing, failed delivery, translation HTTP
requests/failures, and real local OCR when Tesseract is installed. Fixtures are
generated locally; tests use a local fake translation server and no real Telegram
account or third-party user content. Provider
translation quality and the deployed Telegram/Render environment still need a
test upload after configuration.
