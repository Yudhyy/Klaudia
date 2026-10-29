# syntax=docker/dockerfile:1

# Build the uv workspace once; git is needed for the pinned toon-format dependency.
FROM python:3.11-slim-bookworm AS builder

COPY --from=ghcr.io/astral-sh/uv:0.13.0 /uv /uvx /bin/

ENV UV_PROJECT_ENVIRONMENT=/opt/venv \
    UV_PYTHON_DOWNLOADS=0 \
    UV_LINK_MODE=copy \
    UV_COMPILE_BYTECODE=1

RUN apt-get update \
    && apt-get install -y --no-install-recommends git ca-certificates \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Copy workspace manifests first so dependency layers can be cached.
COPY pyproject.toml uv.lock ./
COPY klaudia/pyproject.toml ./klaudia/pyproject.toml
COPY mcp-ledger/pyproject.toml ./mcp-ledger/pyproject.toml
COPY packages/ledger-core/pyproject.toml ./packages/ledger-core/pyproject.toml

RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-dev --no-install-workspace

COPY . /app

# Install the workspace packages after their source files are available.
# The root workspace project is intentionally not installed as a package;
# FastAPI imports app/ from /app; agent, ledger core and MCP are workspace packages.
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --locked --no-dev --no-install-project --no-editable


FROM python:3.11-slim-bookworm AS runtime

ENV VIRTUAL_ENV=/opt/venv \
    PATH="/opt/venv/bin:${PATH}" \
    PYTHONPATH=/app \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

RUN groupadd --system --gid 10001 klaudia \
    && useradd --system --uid 10001 --gid 10001 --home-dir /home/klaudia --create-home klaudia

WORKDIR /app

COPY --from=builder --chown=10001:10001 /opt/venv /opt/venv
COPY --from=builder --chown=10001:10001 /app /app

USER 10001:10001

EXPOSE 8000

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
