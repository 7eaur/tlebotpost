FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PORT=8080

WORKDIR /app

COPY pyproject.toml README.md ./
COPY app ./app
COPY scripts ./scripts
COPY db ./db
RUN pip install --no-cache-dir .

RUN mkdir -p /app/data/media && chmod 700 /app/data

EXPOSE 8080

HEALTHCHECK --interval=30s --timeout=10s --start-period=25s --retries=3 \
    CMD python -m app.health_v2

CMD ["python", "scripts/run_service.py", "--env-file", "/dev/null"]
