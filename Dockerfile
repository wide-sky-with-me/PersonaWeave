# PersonaWeave - single-container internal deployment.
#
# Builds the Web Studio frontend, then packages it together with the FastAPI backend into one
# image. Model weights (~2.5GB) and runtime data (SQLite DBs, staged/accepted images) are NOT
# baked into the image - mount them as volumes (see docker-compose.yml) so the image stays small
# and re-buildable without re-downloading models every time.

# ---- Stage 1: build the Web Studio frontend ----
FROM node:22-slim AS frontend

WORKDIR /app/web
# Node 22.21+ only honors HTTP(S)_PROXY for fetch after this explicit opt-in.
ENV NODE_USE_ENV_PROXY=1

# Pin the same pnpm major version CI uses (see .github/workflows/ci.yml) instead of whatever
# corepack's default resolves to, so local/CI/Docker builds stay reproducible.
RUN corepack enable && corepack prepare pnpm@10 --activate

COPY web/package.json web/pnpm-lock.yaml ./
RUN pnpm install --frozen-lockfile

COPY web/ ./
RUN pnpm build

# ---- Stage 2: Python backend runtime ----
FROM python:3.11-slim AS runtime

RUN apt-get update && apt-get install -y --no-install-recommends \
    libgomp1 \
    curl \
    && rm -rf /var/lib/apt/lists/*

COPY --from=ghcr.io/astral-sh/uv:0.12.5 /uv /uvx /usr/local/bin/

ENV PYTHONUNBUFFERED=1 \
    UV_LINK_MODE=copy \
    UV_PROJECT_ENVIRONMENT=/app/.venv \
    PATH="/app/.venv/bin:${PATH}" \
    UV_HTTP_TIMEOUT=600

WORKDIR /app
VOLUME ["/app/models", "/app/data", "/app/outputs"]
EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s --start-period=300s --retries=3 \
    CMD curl -fsS http://localhost:8000/health || exit 1
COPY docker-entrypoint.sh /usr/local/bin/docker-entrypoint.sh
RUN chmod +x /usr/local/bin/docker-entrypoint.sh
ENTRYPOINT ["docker-entrypoint.sh"]
CMD ["uvicorn", "api.app:app", "--host", "0.0.0.0", "--port", "8000"]

# Native CPU image for Linux ARM64/AMD64; other dependency pins still come from uv.lock.
# Decision: .agents/notes/platform/20261013-pin-cpu-runtime-and-ignore-dev-overrides.md
FROM runtime AS cpu
COPY pyproject.toml uv.lock ./
COPY scripts/container_requirements.py /tmp/container_requirements.py
RUN uv export --frozen --no-dev --no-emit-project --no-hashes > /tmp/locked-requirements.txt \
    && python /tmp/container_requirements.py /tmp/locked-requirements.txt /tmp/cpu-requirements.txt \
    && uv venv /app/.venv \
    && uv --no-config pip install --python /app/.venv/bin/python --no-deps -r /tmp/cpu-requirements.txt
COPY . .
COPY --from=frontend /app/web/dist ./web/dist
# Project installation must not restore CUDA dependencies from metadata.
RUN uv --no-config pip install --python /app/.venv/bin/python --no-deps . \
    && python -c "import cv2, insightface, onnxruntime; from api.app import app; assert 'CPUExecutionProvider' in onnxruntime.get_available_providers()"

# Keep the existing CUDA development lock as the default (last) build target.
FROM runtime AS backend
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project
COPY . .
COPY --from=frontend /app/web/dist ./web/dist
RUN uv sync --frozen --no-dev
