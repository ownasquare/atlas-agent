FROM ghcr.io/astral-sh/uv:0.11.29 AS uv

FROM python:3.12-slim

COPY --from=uv /uv /uvx /bin/

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    PATH="/app/.venv/bin:$PATH" \
    ATLAS_DATA_DIR=/app/.atlas/data \
    ATLAS_WORKSPACE_DIR=/app/.atlas/workspace \
    ATLAS_CODE_EXECUTION_BACKEND=disabled

WORKDIR /app

RUN addgroup --system atlas && adduser --system --ingroup atlas --home /app atlas

COPY pyproject.toml uv.lock README.md LICENSE ./
RUN uv sync --frozen --no-dev --no-install-project --extra anthropic

COPY src ./src
RUN uv sync --frozen --no-dev --extra anthropic \
    && mkdir -p /app/.atlas/data /app/.atlas/workspace \
    && chown -R atlas:atlas /app/.atlas

USER atlas
EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=3s --start-period=20s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/api/health', timeout=2)"

CMD ["atlas", "serve", "--host", "0.0.0.0", "--port", "8000"]
