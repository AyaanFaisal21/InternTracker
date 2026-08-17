# Multi-stage: `web` is slim (stdlib server only); `poller` adds headless
# Chromium for the browser detector tier.
#
#   docker build --target web    -t shortlist-web .
#   docker build --target poller -t shortlist-poller .

FROM python:3.13-slim AS base
WORKDIR /app
ENV PYTHONUNBUFFERED=1 \
    INTAKE_DB=/data/intake.db \
    INTAKE_WATCHLIST=/app/config/watchlist.yaml
COPY pyproject.toml ./
COPY src ./src
COPY config ./config
COPY scripts ./scripts
RUN pip install --no-cache-dir ".[postgres,email]"

FROM base AS web
ENV INTAKE_BIND=0.0.0.0
EXPOSE 8642
CMD ["intake", "serve", "-p", "8642"]

FROM base AS poller
RUN pip install --no-cache-dir playwright \
    && playwright install --with-deps chromium \
    && rm -rf /var/lib/apt/lists/*
# --no-verify until the verifier policy + API key are configured; override
# the command in compose to enable verification.
CMD ["intake", "loop", "-i", "120", "--no-verify"]

# React frontend, built once at image build. Compose copies /fe/dist into
# the volume Caddy serves; this stage never runs as a live service.
FROM node:22-slim AS frontend
WORKDIR /fe
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci
COPY frontend ./
RUN npm run build
