"""Entry point for expendable, resource-bounded document/media processing."""
import json
import random
import sys
from pathlib import Path


def main():
    root = Path(sys.argv[1])
    # Set limits before loading NumPy, XML/image libraries, or any uploaded data.
    import resource
    # FFmpeg's shared libraries also count toward virtual address space.
    resource.setrlimit(resource.RLIMIT_AS, (1024 * 1024 * 1024,) * 2)
    resource.setrlimit(resource.RLIMIT_CPU, (45, 45))
    resource.setrlimit(resource.RLIMIT_FSIZE, (96 * 1024 * 1024,) * 2)
    resource.setrlimit(resource.RLIMIT_NOFILE, (128, 128))
    try:
        from io import BytesIO
        from PIL import Image
        from file_formats import (ContentLimitError, ContentUnavailable, MAX_OUTPUT, pack_artifact,
                                  process_content, unpack_artifact)
        import glitch_bot
        config = json.loads((root / "job.json").read_text(encoding="utf-8"))
        random.seed(config["seed"])
        extension, level = config["extension"], config["level"]
        artifacts = [unpack_artifact(a) for a in config["artifacts"]]

        def render(image, source):
            data, recipe = glitch_bot.glitch_image(b"", level, "PNG", artifacts, image, source)
            with Image.open(BytesIO(data)) as decoded:
                return decoded.convert("RGB"), recipe

        result, recipe, artifact, notices, extension = process_content(
            root / ("input" + extension), extension, level, artifacts, render)
        if len(result) > MAX_OUTPUT:
            raise ContentLimitError("The fused file exceeds the 16 MB output limit.")
        (root / "output").write_bytes(result)
        metadata = {"recipe": recipe, "artifact": pack_artifact(artifact),
                    "notices": notices, "extension": extension}
    except Exception as exc:
        from file_formats import ContentLimitError, ContentUnavailable
        is_limit = isinstance(exc, (ContentLimitError, MemoryError))
        message = (str(exc) if isinstance(exc, (ContentLimitError, ContentUnavailable)) else
                   "Content processing exceeded its memory budget. Try a smaller file."
                   if isinstance(exc, MemoryError) else
                   f"Content processing failed ({type(exc).__name__}).")
        metadata = {"error": "limit" if is_limit else "decode", "message": message}
    (root / "result.json").write_text(json.dumps(metadata), encoding="utf-8")


if __name__ == "__main__":
    main()
