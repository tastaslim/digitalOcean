# ── Stage 1: builder ──────────────────────────────────────────────────────
# Install dependencies into an isolated venv so only the compiled packages
# are carried into the runtime image — no pip, no build cache.
FROM python:3.13-slim-bookworm AS builder

WORKDIR /build

COPY requirements.txt .

RUN python -m venv /venv && \
    /venv/bin/pip install --no-cache-dir --upgrade pip && \
    /venv/bin/pip install --no-cache-dir -r requirements.txt


# ── Stage 2: runtime ──────────────────────────────────────────────────────
FROM python:3.13-slim-bookworm

# Disable .pyc files and enable unbuffered stdout/stderr for clean container logs
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PATH="/venv/bin:$PATH"

# Non-root user — principle of least privilege
RUN addgroup --system appgroup && \
    adduser --system --ingroup appgroup --no-create-home appuser

WORKDIR /app

# Copy only the venv from the builder — no pip or build toolchain in the image
COPY --from=builder /venv /venv

# Copy application source (tests, docs, secrets excluded via .dockerignore)
COPY app/ ./app/

# Persistent volume mount point for the SQLite mismatch database.
# Owned by appuser so the process can write without root.
RUN mkdir -p /data && chown appuser:appgroup /data

USER appuser

EXPOSE 8000

# Liveness probe — /metrics is lightweight and exercises the full import chain
HEALTHCHECK --interval=30s --timeout=5s --start-period=15s --retries=3 \
    CMD python -c \
        "import urllib.request; urllib.request.urlopen('http://localhost:8000/metrics')" \
        || exit 1

# Single worker: metricsStore, shadowPool, and runtimeConfig are in-process
# singletons. Multiple workers would give each its own isolated state and break
# the shared metrics. Use a reverse proxy (nginx/Caddy) for horizontal scale.
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
