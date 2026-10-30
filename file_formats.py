"""Decode, fuse, and rebuild real document/media content in an isolated worker."""
import base64
import json
import math
import os
import random
import re
import shutil
import signal
import subprocess
import sys
import tempfile
import wave
from io import BytesIO
from pathlib import Path
from zipfile import ZipFile, ZIP_DEFLATED

import numpy as np
from PIL import Image, ImageChops, ImageDraw, ImageOps

from artifact_memory import Artifact, open_image
from content_fusion import donor_signal, fuse_audio, fuse_text, merge_traces, trace
from language_glitch import font
from memory_effects import glitch_text

DOCUMENT_EXTENSIONS = (".docx", ".pdf")
AUDIO_EXTENSIONS = (".wav", ".mp3", ".ogg", ".oga", ".flac", ".m4a", ".aac", ".opus", ".aiff", ".aif")
VIDEO_EXTENSIONS = (".mp4", ".mov", ".mkv", ".webm", ".avi", ".gif")
CONTENT_EXTENSIONS = DOCUMENT_EXTENSIONS + AUDIO_EXTENSIONS + VIDEO_EXTENSIONS
MAX_PAGES = 12
MAX_AUDIO_SECONDS = 60
MAX_VIDEO_SECONDS = 12
MAX_OUTPUT = 16 * 1024 * 1024
SAMPLE_RATE = 22050
VIDEO_FPS = 8


class ContentLimitError(ValueError):
    pass


class ContentUnavailable(RuntimeError):
    pass


def pack_artifact(artifact):
    return {"image": base64.b64encode(artifact.image).decode(), "text": list(artifact.text),
            "sample": base64.b64encode(artifact.sample).decode(),
            "signal": base64.b64encode(artifact.signal).decode()}


def unpack_artifact(value):
    return Artifact(base64.b64decode(value["image"]), tuple(value["text"]),
                    base64.b64decode(value["sample"]), base64.b64decode(value.get("signal", "")))


def run_content_job(data, extension, level, artifacts):
    """A wall deadline kills the entire decoder process group, including FFmpeg."""
    if extension not in CONTENT_EXTENSIONS:
        raise ContentUnavailable("Unsupported content format.")
    with tempfile.TemporaryDirectory(prefix="glitch-content-") as directory:
        root = Path(directory)
        (root / ("input" + extension)).write_bytes(data)
        config = {"extension": extension, "level": level, "seed": random.getrandbits(64),
                  "artifacts": [pack_artifact(a) for a in artifacts]}
        (root / "job.json").write_text(json.dumps(config), encoding="utf-8")
        env = dict(os.environ, BOT_TOKEN="", MEMORY_DB=str(root / "worker.sqlite3"),
                   OPENBLAS_NUM_THREADS="1", OMP_NUM_THREADS="1", MKL_NUM_THREADS="1")
        command = [sys.executable, str(Path(__file__).with_name("format_worker.py")), str(root)]
        process = subprocess.Popen(command, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                                   env=env, start_new_session=True)
        try:
            process.wait(timeout=60)
        except subprocess.TimeoutExpired:
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            process.wait()
            raise ContentLimitError("Content processing reached 60 seconds. Try a smaller file.")
        metadata_path = root / "result.json"
        if not metadata_path.exists():
            # CPU/address-space limits may stop the worker before it can report.
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            raise ContentLimitError("Content processing exceeded its resource budget. Try a smaller file.")
        metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
        if metadata.get("error"):
            error = ContentLimitError if metadata["error"] == "limit" else ContentUnavailable
            raise error(metadata["message"])
        result = root / "output"
        if result.stat().st_size > MAX_OUTPUT:
            raise ContentLimitError("The fused file exceeded the 16 MB output limit.")
        return (result.read_bytes(), metadata["recipe"], unpack_artifact(metadata["artifact"]),
                metadata["notices"], metadata["extension"])


def run_tool(args, timeout=20):
    if not shutil.which(args[0]):
        raise ContentUnavailable("A required local decoder is missing; use the Docker setup.")
    try:
        return subprocess.run(args, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                              stderr=subprocess.DEVNULL, timeout=timeout, check=True).stdout
    except subprocess.TimeoutExpired:
        raise ContentLimitError("A document/media decoder reached its time limit. Try a smaller file.")
    except subprocess.CalledProcessError as exc:
        if exc.returncode in (-signal.SIGKILL, -signal.SIGXCPU, -signal.SIGXFSZ):
            raise ContentLimitError("A document/media decoder exceeded its resource budget. Try a smaller file.")
        raise ContentUnavailable("The file could not be decoded locally.")


def contact_sheet(images):
    if not images:
        return None
    indices = np.linspace(0, len(images) - 1, min(16, len(images)), dtype=int)
    sheet = Image.new("RGB", (320, 320))
    for i, index in enumerate(indices):
        item = images[index]
        image = open_image(item.read_bytes()) if isinstance(item, Path) else item
        sheet.paste(ImageOps.fit(image.convert("RGB"), (80, 80)), (i % 4 * 80, i // 4 * 80))
    return sheet


def docx_content(data, level, artifacts, render):
    from lxml import etree
    namespace = {"w": "http://schemas.openxmlformats.org/wordprocessingml/2006/main"}
    with ZipFile(BytesIO(data)) as archive:
        entries = archive.infolist()
        if (len(entries) > 2048 or sum(i.file_size for i in entries) > 32 * 1024 * 1024
                or any(i.file_size > 12 * 1024 * 1024 for i in entries)):
            raise ContentLimitError("DOCX expands beyond 32 MB or contains too many parts.")
        if len({i.filename for i in entries}) != len(entries):
            raise ContentUnavailable("DOCX contains duplicate package entries.")
        roots, paragraphs = {}, []
        for info in entries:
            if re.fullmatch(r"word/(document|header\d*|footer\d*|footnotes|endnotes)\.xml", info.filename):
                root = etree.fromstring(archive.read(info), parser=etree.XMLParser(
                    resolve_entities=False, no_network=True, load_dtd=False))
                if root.getroottree().docinfo.doctype:
                    raise ContentUnavailable("DOCX XML with a DTD is unsupported.")
                roots[info.filename] = root
                paragraphs.extend(root.xpath(".//w:p", namespaces=namespace))
        if "word/document.xml" not in roots:
            raise ContentUnavailable("DOCX has no main document part.")
        texts = ["".join(p.xpath(".//w:t/text()", namespaces=namespace)) for p in paragraphs]
        original_text = "\n".join(texts)
        if len(original_text) > 131072 or len(paragraphs) > 4000 or any(len(t) > 16384 for t in texts):
            raise ContentLimitError("DOCX text exceeds 128K characters, 4000 paragraphs, or 16K per paragraph.")
        media = [i.filename for i in entries if i.filename.startswith("word/media/")
                 and i.filename.lower().endswith((".png", ".jpg", ".jpeg", ".bmp", ".tif", ".tiff"))]
        images = [open_image(archive.read(name)) for name in media[:12]]
        source = trace(data, original_text, contact_sheet(images))
        eligible = [i for i, text in enumerate(texts) if text.strip()]
        count = min(512, len(eligible), max(1, int(len(eligible) * (.2 + level * .06))))
        chosen = random.sample(eligible, count)
        labels = ["DOCX run weave"]
        for index in chosen:
            nodes = paragraphs[index].xpath(".//w:t", namespaces=namespace)
            rewritten, inherited = glitch_text(texts[index], artifacts, level)
            rewritten, maths = fuse_text(rewritten, source, artifacts, level)
            rewritten = re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f]", "", rewritten)
            weights = [len(node.text or "") for node in nodes]
            total, cursor = max(1, sum(weights)), 0
            for i, node in enumerate(nodes):
                end = len(rewritten) if i == len(nodes) - 1 else cursor + int(len(rewritten) * weights[i] / total)
                node.text = rewritten[cursor:end]
                node.set("{http://www.w3.org/XML/1998/namespace}space", "preserve")
                cursor = end
            texts[index] = rewritten
            labels.extend(inherited + maths)
        replacements = {name: etree.tostring(root, encoding="UTF-8", xml_declaration=True)
                        for name, root in roots.items()}
        rendered_images = []
        for name, image in zip(media[:12], images):
            image, effects = render(image, source)
            rendered_images.append(image)
            output = BytesIO()
            # Preserve the package's declared image type and relationship IDs.
            suffix = Path(name).suffix.lower()
            image.save(output, {".jpg": "JPEG", ".jpeg": "JPEG", ".png": "PNG",
                                ".bmp": "BMP", ".tif": "TIFF", ".tiff": "TIFF"}[suffix])
            replacements[name] = output.getvalue()
            labels.extend(effects)
        output = BytesIO()
        with ZipFile(output, "w", compression=ZIP_DEFLATED) as rebuilt:
            for info in entries:
                rebuilt.writestr(info.filename, replacements.get(info.filename, archive.read(info)),
                                 compress_type=ZIP_DEFLATED)
        result = output.getvalue()
    artifact = merge_traces(source, trace(result, "\n".join(texts), contact_sheet(rendered_images)))
    notices = ["DOCX: transformed text runs and up to 12 embedded raster images."]
    return result, list(dict.fromkeys(labels)), artifact, notices, ".docx"


def pdf_content(path, level, artifacts, render):
    root = path.parent
    info = run_tool(["pdfinfo", str(path)]).decode("utf-8", errors="replace")
    match = re.search(r"^Pages:\s+(\d+)", info, re.MULTILINE)
    if not match:
        raise ContentUnavailable("PDF page count could not be read.")
    pages = int(match[1])
    if not 1 <= pages <= MAX_PAGES:
        raise ContentLimitError("PDFs must contain 1–12 pages for content fusion.")
    text_path = root / "text.txt"
    run_tool(["pdftotext", "-enc", "UTF-8", str(path), str(text_path)])
    if text_path.stat().st_size > 256 * 1024:
        raise ContentLimitError("PDF extracted text exceeds 256 KB.")
    text = text_path.read_text(encoding="utf-8")
    run_tool(["pdftoppm", "-f", "1", "-l", str(pages), "-scale-to", "960", "-r", "72",
              "-png", str(path), str(root / "page")], timeout=25)
    files = sorted(root.glob("page-*.png"), key=lambda p: int(p.stem.rsplit("-", 1)[1]))
    if len(files) != pages:
        raise ContentUnavailable("PDF pages could not all be rendered.")
    source = trace(path.read_bytes(), text, contact_sheet(files))
    woven, labels = glitch_text(text, artifacts, level)
    woven, maths = fuse_text(woven, source, artifacts, level)
    labels += maths + ["PDF page raster"]
    words = woven.split()
    outputs = []
    field = donor_signal(source, artifacts, max(1, pages * 4))
    for i, file in enumerate(files):
        image, effects = render(open_image(file.read_bytes()), source)
        labels.extend(effects)
        if words:
            start = (i * max(1, len(words) // pages)) % len(words)
            snippet = " ".join(words[start:start + 16])[:180]
            draw = ImageDraw.Draw(image)
            x = int(abs(field[i * 4]) * image.width * .45)
            y = int(abs(field[i * 4 + 1]) * image.height * .8)
            # Text is a real extracted/donor phrase, never generated placeholder text.
            draw.text((x, y), snippet, font=font(max(8, image.width // 45)), fill="magenta")
        outputs.append(image)
    output = BytesIO()
    outputs[0].save(output, "PDF", save_all=True, append_images=outputs[1:], resolution=96)
    result = output.getvalue()
    artifact = merge_traces(source, trace(result, woven, contact_sheet(outputs)))
    return (result, list(dict.fromkeys(labels)), artifact,
            ["PDF pages rasterized: selectable text, links and forms are flattened."], ".pdf")


INPUT_FORMATS = "mov,matroska,webm,avi,gif,wav,mp3,ogg,flac,aac,aiff"


def media_probe(path):
    result = run_tool(["ffprobe", "-v", "error", "-protocol_whitelist", "file,pipe",
                       "-format_whitelist", INPUT_FORMATS, "-show_entries",
                       "format=duration:stream=codec_type,width,height,duration", "-of", "json", str(path)])
    return json.loads(result)


def audio_decode(path, seconds):
    output = path.parent / "decoded.f32"
    run_tool(["ffmpeg", "-nostdin", "-v", "error", "-y", "-filter_threads", "1", "-filter_complex_threads", "1", "-threads", "1",
              "-protocol_whitelist", "file,pipe", "-format_whitelist", INPUT_FORMATS,
              "-i", str(path), "-map", "0:a:0", "-vn", "-t", str(seconds + 1),
              "-ac", "2", "-ar", str(SAMPLE_RATE), "-f", "f32le", str(output)])
    if output.stat().st_size > (seconds * SAMPLE_RATE + SAMPLE_RATE // 20) * 8:
        raise ContentLimitError(f"Audio exceeds the {seconds}-second content limit.")
    samples = np.fromfile(output, dtype="<f4").reshape(-1, 2)
    if not len(samples):
        raise ContentUnavailable("The audio stream contains no samples.")
    return samples


def write_wave(path, samples):
    with wave.open(str(path), "wb") as output:
        output.setnchannels(2)
        output.setsampwidth(2)
        output.setframerate(SAMPLE_RATE)
        output.writeframes((np.clip(samples, -.92, .92) * 32767).astype("<i2").tobytes())


def waveform_image(samples):
    image = Image.new("RGB", (320, 160), "black")
    draw = ImageDraw.Draw(image)
    field = donor_signal(trace(waveform=samples), (), 320)
    draw.line([(i, 80 - int(v * 70)) for i, v in enumerate(field)], fill="cyan", width=2)
    return image


def media_content(path, extension, level, artifacts, render):
    root = path.parent
    probe = media_probe(path)
    streams = probe.get("streams", [])
    is_video = extension in VIDEO_EXTENSIONS
    maximum = MAX_VIDEO_SECONDS if is_video else MAX_AUDIO_SECONDS
    durations = [probe.get("format", {}).get("duration")] + [s.get("duration") for s in streams]
    for duration in durations:
        if duration not in (None, "N/A"):
            value = float(duration)
            if not math.isfinite(value) or value < 0 or value > maximum + .05:
                raise ContentLimitError(f"Content fusion accepts video up to {MAX_VIDEO_SECONDS}s and audio up to {MAX_AUDIO_SECONDS}s.")
    has_audio = any(s.get("codec_type") == "audio" for s in streams)
    samples = audio_decode(path, maximum) if has_audio else None
    if not is_video:
        if samples is None:
            raise ContentUnavailable("No audio stream was found.")
        source = trace(path.read_bytes(), image=waveform_image(samples), waveform=samples)
        output, effects = fuse_audio(samples, source, artifacts, level)
        target = root / "fused.wav"
        write_wave(target, output)
        result = target.read_bytes()
        artifact = merge_traces(source, trace(result, image=waveform_image(output), waveform=output))
        return result, effects, artifact, ["Audio rebuilt as stereo 22.05 kHz PCM WAV."], ".wav"
    video_stream = next((s for s in streams if s.get("codec_type") == "video"), None)
    if not video_stream:
        raise ContentUnavailable("No video stream was found.")
    if video_stream.get("width", 0) * video_stream.get("height", 0) > 12_000_000:
        raise ContentLimitError("Video frames exceed 12 megapixels.")
    frames_dir, fused_dir = root / "frames", root / "fused"
    frames_dir.mkdir()
    fused_dir.mkdir()
    run_tool(["ffmpeg", "-nostdin", "-v", "error", "-y", "-filter_threads", "1", "-filter_complex_threads", "1", "-threads", "1",
              "-protocol_whitelist", "file,pipe", "-format_whitelist", INPUT_FORMATS,
              "-i", str(path), "-map", "0:v:0", "-an", "-t", str(maximum + 1),
              "-vf", f"fps={VIDEO_FPS},scale=480:480:force_original_aspect_ratio=decrease:force_divisible_by=2,setsar=1",
              "-frames:v", str(maximum * VIDEO_FPS + 1), "-threads", "1",
              str(frames_dir / "%04d.png")])
    frames = sorted(frames_dir.glob("*.png"))
    if len(frames) > maximum * VIDEO_FPS:
        raise ContentLimitError(f"Video exceeds the {maximum}-second content limit.")
    if not frames:
        raise ContentUnavailable("Video contains no decodable frames.")
    source = trace(path.read_bytes(), image=contact_sheet(frames), waveform=samples)
    field = donor_signal(source, artifacts, len(frames))
    order = list(range(len(frames)))
    labels = ["temporal feedback"]
    if level >= 4 and len(frames) > 2:
        # Permute inside temporal neighbourhoods, retaining the overall duration.
        width = min(len(frames), 2 + level)
        for i in range(0, len(frames), width):
            order[i:i + width] = [i + int(j) for j in np.argsort(field[i:i + width], kind="stable")]
        labels.append("content time permutation")
    ghosts, previews = [], []
    style = random.choice(("blend", "scan", "difference"))
    for i, index in enumerate(order):
        image, effects = render(open_image(frames[index].read_bytes()), source)
        labels.extend(effects)
        if ghosts:
            donor = random.choice(ghosts)
            if style == "blend":
                image = Image.blend(image, donor, .12 + level * .025)
            elif style == "difference":
                image = Image.blend(image, ImageChops.difference(image, donor), .2 + level * .03)
            else:
                row = int(abs(field[i]) * max(0, image.height - 1))
                end = min(image.height, row + max(1, image.height // (15 - level)))
                image.paste(donor.crop((0, row, image.width, end)), (0, row))
        ghosts.append(image.copy())
        ghosts = ghosts[-3:]
        if i % max(1, len(frames) // 12) == 0:
            previews.append(image.resize((160, 160)))
        image.save(fused_dir / f"{i + 1:04d}.png")
    command = ["ffmpeg", "-nostdin", "-v", "error", "-y", "-cpucount", "1", "-filter_threads", "1", "-filter_complex_threads", "1", "-threads", "1",
               "-framerate", str(VIDEO_FPS), "-i", str(fused_dir / "%04d.png")]
    audio_output = None
    if samples is not None:
        audio_output, audio_effects = fuse_audio(samples, source, artifacts, level)
        labels.extend(audio_effects)
        write_wave(root / "sound.wav", audio_output)
        command += ["-i", str(root / "sound.wav"), "-c:a", "aac", "-b:a", "96k"]
    target = root / "fused.mp4"
    command += ["-c:v", "libx264", "-pix_fmt", "yuv420p", "-threads", "1", "-preset", "veryfast", "-tune", "zerolatency",
                "-x264-params", "threads=1:lookahead_threads=1:sync-lookahead=0:rc-lookahead=0",
                "-crf", "26", "-movflags", "+faststart", "-t", str(len(frames) / VIDEO_FPS), str(target)]
    run_tool(command, timeout=25)
    result = target.read_bytes()
    artifact = merge_traces(source, trace(result, image=contact_sheet(previews), waveform=audio_output))
    return (result, list(dict.fromkeys(labels)), artifact,
            ["Video rebuilt as MP4, 8 fps, up to 480 px; any audio is fused too."], ".mp4")


def process_content(path, extension, level, artifacts, render):
    if extension == ".docx":
        return docx_content(path.read_bytes(), level, artifacts, render)
    if extension == ".pdf":
        return pdf_content(path, level, artifacts, render)
    return media_content(path, extension, level, artifacts, render)
