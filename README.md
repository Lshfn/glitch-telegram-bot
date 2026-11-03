# Glitch Telegram bot

A poetic glitch interpretation of what is happening to Telegram in Russia.
The bot turns interruptions in communication into changes in the files themselves:
images break apart, fragments of earlier uploads return, and words slip into other languages.

![A glitched image produced by the bot](<glitched_image%20(31).jpg>)

## How it works

The bot makes images inherit surface detail, ghost layers, contours
and snippets from previous uploads. Documents, sound and moving images now take
part in the same memory. A paragraph can become a displacement field for an
image; an audio waveform can become a document's word-order pattern; video frames
can return inside later uploads.

The original eleven image effects, JPEG databend, phrase inheritance, translations
and byte chaos remain. Each render chooses a different recipe and order.
Processing is automatic: the bot selects the route from the file type, and its
content and earlier uploads supply the transformation material. Intensity varies
from 1 to 12, with quieter and medium-strength recipes more frequent. Repeated
content gently biases the draw towards stronger effects, without locking later
uploads at maximum intensity. A violent render can be followed by a quiet one.

Automatic image processing excludes strip effects, hard rectangular cuts,
rectangular noise clusters, JPEG byte corruption, spectral crossover and modular
lattice scrambling. Those earlier
effects remain in the engine for internal use. The automatic variations work
through regions grown from the pictures' color, edges and texture, including
erosion that spreads neighbouring light and dark detail across the surface.

Memory enters through multiscale blending, with palimpsest, texture graft, ghost
field and channel variations. Donor proportions are retained when
fitting the image; the revealed area follows the current and remembered images.
Texture grafts adapt the remembered image's low-frequency color to the current
picture. Automatic memory prefers the original thumbnails so old cutout effects
do not recirculate from an entry's processed thumbnail. Processed traces continue
to drive mathematical fusion. Mathematical effects and editing agents use
image-guided regions too.
There are no generated circular apertures or default sticker/window crops.
Earlier documents can return as readable phrases and page material, and earlier
audio as projected signals.

Some images use small autonomous editing agents inspired by
[RRR's 0..1](https://rulesrulesrules.studio/art/0-1). They read changing edge and
brightness maps, move locally and interact through smudging, sharpening,
restoration, sampling older material, prism shifts, texture bleed and contour ink.
Selected image details resist their edits. Local pixel values can change an agent's
next tool and brush radius. This is a bounded adaptation of the tool-interaction
idea; the reference images and implementation are not copied into the bot.

There is no setup dialogue, processing command menu or technical caption on
returned files. `/start` and `/help` reply with a single symbol. Other text is
ignored unless it is one of the optional memory/translation controls below.
The old default Telegram command menu is cleared at startup in both webhook and
polling deployments. Existing `/mode` and `/chaos` preferences are ignored.

## Content fusion

Transformations work on decoded file content automatically and
rebuild the file in the format below. Ordinary Telegram audio, voice messages,
animations and video notes are accepted as well as file attachments.

| Input | Material used | Output |
| --- | --- | --- |
| JPEG, PNG, WebP, BMP, TIFF | Pixels, file-byte projection, previous images/text/signals | JPEG for JPEG inputs; PNG otherwise |
| DOCX | Text runs in the body, tables, headers, footers, footnotes/endnotes; up to 12 embedded raster images | DOCX with run styling and package relationships retained |
| PDF | Every page, extracted text, page contact sheet, file-byte projection | Raster PDF with the same page count; selectable text, links and forms are flattened |
| WAV, MP3, OGG/Opus, FLAC, M4A, AAC, AIFF | Decoded waveform, waveform drawing, file-byte projection | Stereo 22.05 kHz, 16-bit PCM WAV |
| MP4, MOV, MKV, WebM, AVI, GIF | Decoded frames, contact sheet, any audio, temporal feedback | H.264 MP4 with adaptive frame rate, up to 480 px; audio becomes AAC |
| Other files | Payload bytes and earlier content projections | Same extension; intentionally destructive |

The additional mathematical variations include:

- Fourier magnitude/phase crossover between the source and donor signals.
- Displacement and modular coordinate maps driven by decoded material.
- Gray–Scott reaction–diffusion initialized with source pixels and donor content.
- Bitplane weaving between images, waveforms and file-derived fields.
- Audio spectral vocoding, content-derived convolution kernels, grain
  permutations, phase modulation and wavefolding.
- Logistic-map word permutations using the source and earlier uploads.
- Occasional short video frame permutations and delayed-frame feedback.
- Block motion estimation that advects an old frame, with low residual refresh,
  blooming and periodic recovery. A clip keeps its image recipe across frames.
- Actual codec datamoshing on some clips: encode a temporary MPEG-4 Part 2 stream
  without B frames, remove later I-frame payloads while retaining the first,
  decode it, restore removed time slots with a held preceding frame, then rebuild
  a playable H.264 MP4 with the processed audio. Decoder failures retain the
  decoded-frame effects; resource limit failures are still reported. See the
  [FFmpeg bitstream documentation](https://www.ffmpeg.org/ffmpeg-bitstream-filters.html).
- Finite-field multiplication, convolution modulo 256 and modular weaving for
  generic payload bytes.

Randomness selects the recipe. The uploaded material supplies the numerical
fields, phases, kernels and words. This also works on the first upload, using
its own content; later uploads contribute additional material. Image mathematics
uses a working grid up to 768 px and returns to the image's processing dimensions.

Existing image/text effects are combined with content fusion. Byte chaos remains
the route for unknown files and a fallback when a document/media decoder is
unavailable or fails. Those binary outputs may be unreadable. Processing notices
go to server logs instead of file captions. A processing-limit overrun is still
reported without returning a truncated substitute. The earlier raw route also
remains available internally to the engine; users do not choose processing modes.

## Memory

Histories belong to **one sender in one chat**. Nothing crosses between users or
chats. The default archive holds up to **512 distinct file traces per history**,
8192 in total, for **180 days since the last upload of that content**. Repeated
content updates its entry while retaining its original arrival time and archive
priority. Recalling an old file does not reset its expiry.

A quarter of the capacity is reserved for recent uploads. The rest is a random
priority reservoir, so new uploads do not simply push every older file out. Each
render receives at most 16 traces: roughly a quarter from the recent part of the
history, a quarter from the middle and half from its oldest part. Recently
recalled files have reduced selection weight for six hours. The actual upload
is excluded from donor selection when other memories exist. Only the selected
traces are loaded into the worker, even when the archive holds hundreds.

Image memory can combine one to three distinct earlier images, with independently
selected blending styles and smaller regions when several layers are used.
Only two small re-encoded thumbnails/contact sheets (original and transformed,
each up to 320 px), up to five text excerpts, up to
4 KB of payload fragments and up to 4 KB of projected content signals are
retained. These traces combine original and transformed material. Complete files
and filenames are not stored in the history. Temporary decoder files are deleted
after processing. Effects from earlier outputs can recur and drift through later
ones. Existing SQLite histories migrate automatically and retain their old traces.
Old entries without an original thumbnail use their existing image trace until
new uploads supply the separate original and transformed thumbnails.

The optional `/forget` command clears that history, intensity and translation
preference, with a brief acknowledgement. It is not advertised in a bot menu.
Without cloud credentials, storage is SQLite at `data/artifacts.sqlite3`
(override `MEMORY_DB`). Render's free ephemeral filesystem loses this local
archive on restart/redeploy. Changing the retention setting alone cannot retain
files on an ephemeral filesystem. Expiry is applied on access, not by a background task.

### Free persistent memory on Render

Keep the bot on **Render Free** and use a **Turso Free libSQL database** for the
archive. Turso currently offers 5 GB of free database storage with no credit card
required: see [current pricing](https://turso.tech/pricing). This project does not
provision paid services or enable overage billing. Keep the provider account on
its Free plan and do not enable paid overages.

1. Create a free Turso account and a **libSQL** database. The current adapter uses
   the `libsql` driver, not the newer Turso rewrite engine.
2. Add `TURSO_DATABASE_URL` (`libsql://…`) and `TURSO_AUTH_TOKEN` privately in the
   existing Render service's Environment settings. See
   [Turso's Python setup](https://docs.turso.tech/sdk/python/quickstart).
3. Deploy the bot with those two settings. It writes directly to the remote
   database; the archive no longer depends on Render's local filesystem.

Only the bounded traces described above go into that database. Original files
and filenames are not retained. The same sender/chat isolation, expiry, random
selection and `/forget` deletion apply to both backends. Keep the database token
out of source code, bot messages and public configuration. Decoder workers do
not receive cloud credentials.

Both settings are required together. Invalid credentials, network outages or
provider quota limits cause an error instead of silently switching to disposable
local memory. Free quotas still limit storage and activity; monitor them in the
provider dashboard. No live Turso database is created or connected by this repo.
Switching to a new database does not automatically copy any existing local
archive, and files already lost during earlier Render restarts cannot be recovered.

Owner settings in `.env.example` are `MEMORY_DAYS=180`,
`MEMORY_PER_HISTORY=512`, `MEMORY_TOTAL=8192`, and `MEMORY_DONORS=16` (maximum 32).
See [Render's storage limitations](https://render.com/docs/free#local-files-lost-on-redeploy).

## Random translations

Set `TRANSLATE_URL` to the root URL of a LibreTranslate-compatible service, e.g.
`https://your-translator.example`, with **no** `/translate` suffix. Set
`TRANSLATE_API_KEY` if it requires authentication. No public service or API key is
silently selected. You can self-host LibreTranslate; see
<https://docs.libretranslate.com/guides/installation/>.

The optional `/translate on` command opts that sender/chat into sending
selected snippets to the configured provider; images are OCR'd locally and only
recognized text is sent, never the image itself. `/translate off` stops this.
Translation is off by default; automatic processing does not enable it or change
an existing opt-in. Without a service, memory effects still work. These controls
are not advertised in a bot menu. Group members cannot enable translation for
each other's uploads.

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
recorded in server logs and do not stop ordinary glitches.

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
`TRANSLATE_API_KEY` there. Cloud-API uploads are limited to 20 MB, text files to 512 KB,
images to 12 megapixels and processing resolution to 1600 px on the longest edge.
Heavy processing runs off the event loop; at most two uploads run concurrently,
with ordered processing within each sender/chat history. Only one document/media
worker runs at a time. Workers have a 240-second wall deadline, a 180-second CPU
budget and a 1 GB virtual address-space limit (including decoder libraries);
their decoder subprocesses inherit the
limits and are killed with the worker on timeout. Use Docker on other platforms.

Default processing limits are:

| Limit | Default |
| --- | --- |
| Input via Telegram's public Bot API | 20 MB |
| Input via a configured local Bot API server | 100 MB |
| PDF | 100 pages; 2 MB of extracted text |
| Video | 120 seconds, at most 320 processing frames |
| Audio | 180 seconds |
| Output | 48 MB on the public API; 100 MB on the configured local server |

Short videos keep up to 8 fps. Long videos reduce their frame rate to fit the
processing budget (2 fps at the 120-second default maximum). Video resolution is
up to 480 px, or 384 px for clips longer than 30 seconds. Small source frames are
not enlarged. The full timeline and audio are retained. PDF pages are processed
individually and merged with Poppler, without keeping every full page in RAM.
Large file bytes are projected in small chunks for cross-file mathematics.

The owner can adjust `MAX_UPLOAD_MB`, `MAX_OUTPUT_MB`, `MAX_PDF_PAGES`,
`MAX_VIDEO_SECONDS` and `MAX_AUDIO_SECONDS` within the validated ranges. Worker
resource limits still apply. The existing DOCX expansion/text limits (32 MB,
128K characters, 4000 paragraphs and 16K per paragraph) and 12-megapixel image
and video-input frame limits remain.

### Files above the public Telegram API limit

Telegram's public `getFile` endpoint only downloads files up to 20 MB. Larger
uploads require a separately hosted [Telegram Bot API server](https://core.telegram.org/bots/api#using-a-local-bot-api-server),
running with `--local`. The bot supports this through `BOT_API_URL` and
`BOT_API_LOCAL_FILES=true`. Mount the API server's files directory at the same
absolute paths in the bot container; aiogram then reads those local files.
Input/output limits default to 100 MB with that endpoint configured. Downloads
are bounded while they stream, including when Telegram does not supply file size.

Follow Telegram's [migration procedure](https://core.telegram.org/bots/features#local-bot-api),
including cloud `logOut`, before moving an active bot. No API server is started,
no logout is performed, and no existing hosted service is reconfigured by this
code. The default remains Telegram's public API.

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
destructive fallback, automatic intensity and routing, silent file replies,
command-menu cleanup, failed delivery, translation HTTP
requests/failures, recognisable memory proportions, intact/processed thumbnail
migration, historical archive retention and random age mixing, multiple memory
layers, bounded downloads, local Bot API file reads, larger PDF/video/audio,
local editing agents, image-guided masks and multiscale memory,
automatic exclusion of strip/cutout effects, motion feedback,
actual codec I-frame removal and frame-count preservation, and real local OCR
when Tesseract is installed. Fixtures are
generated locally; tests use a local fake translation server and no real Telegram
account or third-party user content. Provider
translation quality and the deployed Telegram/Render environment still need a
test upload after configuration.
