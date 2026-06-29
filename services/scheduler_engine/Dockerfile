# Build context = repo root (see docker-compose.yml).
FROM python:3.11-slim
ENV PYTHONUNBUFFERED=1
WORKDIR /app

# Shared library first (better layer caching).
COPY libs/common /tmp/common
RUN pip install --no-cache-dir /tmp/common

COPY services/scheduler_engine/ /app/
COPY config/ /app/config/

CMD ["python", "main.py"]
