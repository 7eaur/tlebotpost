FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

COPY pyproject.toml README.md ./
COPY app ./app
RUN pip install --no-cache-dir .

RUN useradd --create-home --uid 10001 relay \
    && mkdir -p /app/data \
    && chown -R relay:relay /app
USER relay

HEALTHCHECK --interval=30s --timeout=10s --start-period=20s --retries=3 \
    CMD python -m app.health

CMD ["python", "-m", "app.main"]
