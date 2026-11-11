# syntax=docker/dockerfile:1
ARG BUILD_FROM=ghcr.io/home-assistant/amd64-base-python:3.13-alpine3.22

# ---- frontend -------------------------------------------------------------
FROM node:24-alpine AS frontend
WORKDIR /src
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci --no-audit --no-fund
COPY frontend/ ./
RUN npx tsc --noEmit && npx vite build --outDir /out --emptyOutDir

# ---- app ------------------------------------------------------------------
FROM ${BUILD_FROM}
# S6_KEEP_ENV: the Home Assistant base image runs CMD under s6-overlay, which
# otherwise starts it with an empty environment (losing SUPERVISOR_TOKEN and
# the HINV_* settings below).
ENV S6_KEEP_ENV=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    HINV_FRONTEND_DIR=/app/static \
    HINV_DATA_DIR=/data

WORKDIR /app
COPY server/pyproject.toml server/README.md /app/server/
COPY server/home_inventory_server /app/server/home_inventory_server
RUN pip install /app/server && rm -rf /root/.cache
COPY --from=frontend /out /app/static

# Runs under the base image's s6-overlay init as PID 1.
CMD ["python3", "-m", "home_inventory_server"]
