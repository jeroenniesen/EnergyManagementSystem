# Production image (SPEC §11). Reproducible from uv.lock: `uv sync --frozen` installs the exact
# resolved versions, never a fresh resolve, so the image can't silently drift from what was tested.
# `docker build .` is CI-tested (B-44).

# Stage 1 — build the React/Vite SPA (output: ems/web/static/dist).
FROM node:22-slim AS frontend
WORKDIR /app
COPY ems/web/frontend/package.json ems/web/frontend/package-lock.json ems/web/frontend/
RUN cd ems/web/frontend && npm ci
COPY ems/web/frontend ems/web/frontend
RUN cd ems/web/frontend && npm run build

# Stage 2 — lean Python 3.12 runtime serving the API + the built SPA (no runtime CDN).
FROM python:3.12-slim
WORKDIR /app

# Official static-binary install method (no pip/pipx bootstrap needed).
COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv

# Dependency layer first (cache-friendly): resolved *exactly* from uv.lock, never re-resolved
# (--frozen) and without the dev group (pytest/ruff). All runtime deps ship prebuilt wheels for
# manylinux/amd64+arm64 (checked against uv.lock), so no compiler toolchain is needed here.
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev

COPY ems ./ems
COPY config.yaml ./config.yaml
COPY --from=frontend /app/ems/web/static/dist ./ems/web/static/dist

ENV PATH="/app/.venv/bin:$PATH"

EXPOSE 8080
CMD ["uvicorn", "ems.main:app", "--host", "0.0.0.0", "--port", "8080"]
