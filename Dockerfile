FROM ghcr.io/astral-sh/uv:python3.12-bookworm-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy

WORKDIR /app

COPY pyproject.toml uv.lock uv.toml README.md ./
COPY src ./src
RUN uv sync --frozen --no-dev

COPY comfy-workflows ./comfy-workflows
COPY workflows ./workflows

RUN addgroup --system personaweave \
    && adduser --system --ingroup personaweave personaweave \
    && mkdir -p /app/data \
    && chown -R personaweave:personaweave /app

USER personaweave

EXPOSE 8000 8010
CMD ["uv", "run", "uvicorn", "personaweave.main:app", "--host", "0.0.0.0", "--port", "8000"]
