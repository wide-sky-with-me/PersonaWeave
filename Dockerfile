# PersonaWeave - single-container internal deployment.
#
# Builds the Web Studio frontend, then packages it together with the FastAPI backend into one
# image. Model weights (~2.5GB) and runtime data (SQLite DBs, staged/accepted images) are NOT
# baked into the image - mount them as volumes (see docker-compose.yml) so the image stays small
# and re-buildable without re-downloading models every time.

# ---- Stage 1: build the Web Studio frontend ----
FROM node:22-slim AS frontend

WORKDIR /app/web

# Pin the same pnpm major version CI uses (see .github/workflows/ci.yml) instead of whatever
# corepack's default resolves to, so local/CI/Docker builds stay reproducible.
RUN corepack enable && corepack prepare pnpm@10 --activate

COPY web/package.json web/pnpm-lock.yaml ./
RUN pnpm install --frozen-lockfile

COPY web/ ./
RUN pnpm build

# ---- Stage 2: Python backend runtime ----
FROM python:3.11-slim AS backend

# libgomp1: required by onnxruntime's threading pool.
# curl: used by the container HEALTHCHECK below.
RUN apt-get update && apt-get install -y --no-install-recommends \
    libgomp1 \
    curl \
    && rm -rf /var/lib/apt/lists/*

COPY --from=ghcr.io/astral-sh/uv:0.12.5 /uv /uvx /usr/local/bin/

ENV PYTHONUNBUFFERED=1 \
    UV_LINK_MODE=copy \
    UV_PROJECT_ENVIRONMENT=/app/.venv \
    PATH="/app/.venv/bin:${PATH}" \
    # Several nvidia-*-cu12 wheels (onnxruntime-gpu's CUDA/cuDNN deps) are 500-800MB each;
    # uv's default 30s HTTP timeout is routinely too short for them, so extend it.
    UV_HTTP_TIMEOUT=600

WORKDIR /app

# Install dependencies first (separate layer from source) so editing application code doesn't
# invalidate the dependency-install cache - the nvidia-*-cu12 wheels alone are >1GB.
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project

COPY . .
COPY --from=frontend /app/web/dist ./web/dist
RUN uv sync --frozen --no-dev

# Model weights and runtime data live outside the image; see docker-compose.yml.
VOLUME ["/app/models", "/app/data", "/app/outputs"]

EXPOSE 8000

# start-period is generous because the entrypoint downloads models (~2.5GB, first run only)
# and loads them into the pipeline before uvicorn opens the port at all; failures during this
# window don't count toward the unhealthy threshold. Pre-populating ./models before first
# start avoids the download and brings this down to a normal model-load time.
HEALTHCHECK --interval=30s --timeout=5s --start-period=300s --retries=3 \
    CMD curl -fsS http://localhost:8000/health || exit 1

COPY docker-entrypoint.sh /usr/local/bin/docker-entrypoint.sh
RUN chmod +x /usr/local/bin/docker-entrypoint.sh

ENTRYPOINT ["docker-entrypoint.sh"]
CMD ["uvicorn", "api.app:app", "--host", "0.0.0.0", "--port", "8000"]
