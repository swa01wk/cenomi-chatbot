# ─────────────────────────────────────────────────────────────────────────────
# Cenomi Mall Concierge — Backend
# ─────────────────────────────────────────────────────────────────────────────
# Multi-stage build:
#   builder  — installs all Python dependencies into a venv
#   runtime  — lean image; copies only the venv + app code
# ─────────────────────────────────────────────────────────────────────────────

# ── Stage 1: dependency builder ───────────────────────────────────────────────
FROM python:3.11-slim AS builder

WORKDIR /build

# Install build tools needed for some native dependencies
RUN apt-get update && apt-get install -y --no-install-recommends \
        build-essential \
        gcc \
    && rm -rf /var/lib/apt/lists/*

# Copy only the dependency manifest first (layer caching)
COPY pyproject.toml .

# Create a venv and install all deps including the optional redis extra
RUN python -m venv /opt/venv && \
    /opt/venv/bin/pip install --upgrade pip && \
    /opt/venv/bin/pip install -e ".[redis]"

# ── Stage 2: runtime image ────────────────────────────────────────────────────
FROM python:3.11-slim AS runtime

WORKDIR /app

# Copy the pre-built venv from the builder stage
COPY --from=builder /opt/venv /opt/venv

# Activate venv for all subsequent RUN/CMD calls
ENV PATH="/opt/venv/bin:$PATH" \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

# Copy application source — include all local packages used by the app
COPY app/          ./app/
COPY guardrails/   ./guardrails/
COPY intent/       ./intent/
COPY llm/          ./llm/
COPY response/     ./response/
COPY retrieval/    ./retrieval/
COPY playbooks/    ./playbooks/
COPY data/         ./data/
COPY pyproject.toml .

# Re-install the package in the runtime image so pkg metadata is available
RUN /opt/venv/bin/pip install --no-deps -e ".[redis]"

# Expose the default backend port
EXPOSE 8000

# Health check — polls /api/health every 30 s, allows 60 s for startup
HEALTHCHECK --interval=30s --timeout=10s --start-period=60s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8000/api/health')"

# Run uvicorn; settings override via environment variables
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
