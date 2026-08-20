FROM node:24-bookworm-slim AS frontend-build

WORKDIR /build/frontend
RUN corepack enable && corepack prepare pnpm@11.21.0 --activate

COPY frontend/package.json frontend/pnpm-lock.yaml ./
RUN --mount=type=cache,id=personaweave-pnpm,target=/pnpm/store \
    pnpm config set store-dir /pnpm/store \
    && pnpm install --frozen-lockfile

COPY frontend/ ./
RUN pnpm build


FROM ghcr.io/astral-sh/uv:python3.12-bookworm-slim AS runtime

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PROJECT_ENVIRONMENT=/app/.venv \
    PATH=/app/.venv/bin:$PATH \
    PYTHONPATH=/app/src \
    PERSONAWEAVE_DATABASE_PATH=/app/data/personaweave.sqlite3 \
    PERSONAWEAVE_ASSET_ROOT=/app/data/assets

WORKDIR /app

COPY pyproject.toml uv.lock uv.toml README.md ./
RUN --mount=type=cache,id=personaweave-uv,target=/app/.uv-cache \
    uv sync --frozen --no-dev --no-install-project

COPY src ./src
COPY comfy-workflows ./comfy-workflows
COPY workflows ./workflows
COPY --from=frontend-build /build/src/personaweave/static ./src/personaweave/static

RUN addgroup --system personaweave \
    && adduser --system --ingroup personaweave --home /home/personaweave personaweave \
    && mkdir -p /app/data \
    && chown -R personaweave:personaweave /app/data /home/personaweave \
    && test -f /app/src/personaweave/static/index.html

USER personaweave

EXPOSE 8000
STOPSIGNAL SIGTERM
HEALTHCHECK --interval=15s --timeout=5s --start-period=20s --retries=5 \
    CMD ["python", "-c", "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/api/v1/health', timeout=3)"]

CMD ["uvicorn", "personaweave.main:app", "--host", "0.0.0.0", "--port", "8000"]
