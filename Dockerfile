FROM python:3.11-slim-bookworm

LABEL org.opencontainers.image.title="SeedHound"
LABEL org.opencontainers.image.description="基于 pieces_hash 的高性能异步 PT 辅种引擎"
LABEL org.opencontainers.image.source="https://github.com/Reseed-Puppy/SeedHound"
LABEL org.opencontainers.image.licenses="MIT"

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY src/ ./src/
COPY run.py .

RUN mkdir -p /app/data /app/logs /app/torrents

ENV PYTHONUNBUFFERED=1

STOPSIGNAL SIGINT
CMD ["python", "run.py"]