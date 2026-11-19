# Pipeline image: `docker run <image> [pipeline.run arguments]`, configured only through
# environment variables (DATABASE_URL, LOG_LEVEL, HTTP_TIMEOUT_SECONDS).
#
# Two stages: the builder has uv and resolves dependencies from uv.lock; the final
# image only gets the ready virtual environment, the code and the SQL files.

# Same uv version as used locally, so uv.lock is read exactly the same way.
FROM ghcr.io/astral-sh/uv:0.13.5 AS uv

FROM python:3.12.15-slim-trixie AS builder
COPY --from=uv /uv /usr/local/bin/uv
ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=never
WORKDIR /app

# Dependencies first, in their own layer: it is only rebuilt when uv.lock changes,
# not on every code change.
COPY pyproject.toml uv.lock README.md ./
RUN uv sync --locked --no-dev --no-install-project

# Then the project itself. It stays an editable install, so pipeline.db finds sql/
# next to src/ exactly as in the repository.
COPY src ./src
RUN uv sync --locked --no-dev


FROM python:3.12.15-slim-trixie
WORKDIR /app

# Never run as root inside the container.
RUN useradd --create-home --uid 10001 pipeline

COPY --from=builder /app/.venv ./.venv
COPY src ./src
COPY sql ./sql

ENV PATH="/app/.venv/bin:$PATH" \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

USER pipeline
ENTRYPOINT ["python", "-m", "pipeline.run"]
