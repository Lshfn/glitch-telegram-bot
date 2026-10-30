FROM python:3.13-slim
RUN apt-get update && apt-get install -y --no-install-recommends \
    tesseract-ocr tesseract-ocr-eng tesseract-ocr-rus fonts-noto-core ffmpeg poppler-utils \
    && rm -rf /var/lib/apt/lists/*
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY glitch_bot.py artifact_memory.py language_glitch.py memory_effects.py content_fusion.py file_formats.py format_worker.py ./
ENV PYTHONUNBUFFERED=1
CMD ["python", "glitch_bot.py"]
