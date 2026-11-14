# syntax=docker/dockerfile:1.7
# One multi-stage Dockerfile for every Python service (ADR-005, US-002).
# Build with:  docker build -f deploy/docker/python.Dockerfile --build-arg SERVICE=api --target api .
# Images run as a non-root user, with a read-only root filesystem in Compose/Helm and
# a health check on every daemon.

ARG PYTHON_IMAGE=python:3.13-slim-bookworm

# ---------------------------------------------------------------- build stage
FROM ${PYTHON_IMAGE} AS build
ARG SERVICE
COPY --from=ghcr.io/astral-sh/uv:0.9.7 /uv /usr/local/bin/uv
ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy UV_PYTHON_DOWNLOADS=never
WORKDIR /app
COPY pyproject.toml uv.lock ./
COPY packages ./packages
COPY services ./services
COPY workers ./workers
# Install only the selected workspace member and its dependencies (no dev tools).
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-dev --no-editable --package "tela-${SERVICE}" --python /usr/local/bin/python3

# ---------------------------------------------------------------- runtime base
FROM ${PYTHON_IMAGE} AS runtime
ENV PATH="/app/.venv/bin:$PATH" PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
RUN groupadd --system --gid 10001 tela && useradd --system --uid 10001 --gid tela --home /app tela
WORKDIR /app
COPY --from=build --chown=tela:tela /app/.venv /app/.venv
USER 10001:10001

# ---------------------------------------------------------------- api (COMP-002)
FROM runtime AS api
# Starter pack manifests (data, not code) for `tela-admin load-packs` (US-037).
COPY --chown=tela:tela packs /app/packs
ENV TELA_PACKS_DIR=/app/packs
EXPOSE 8000
HEALTHCHECK --interval=15s --timeout=3s --start-period=20s --retries=3 \
  CMD ["python", "-c", "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8000/healthz', timeout=2).status == 200 else 1)"]
CMD ["gunicorn", "tela_api.main:create_app()", "--worker-class", "uvicorn.workers.UvicornWorker", \
     "--bind", "0.0.0.0:8000", "--workers", "2", "--worker-tmp-dir", "/tmp", "--access-logfile", "-"]

# ---------------------------------------------------------------- migrate (one-shot)
FROM runtime AS migrate
USER root
COPY --chown=tela:tela alembic.ini /app/alembic.ini
COPY --chown=tela:tela migrations /app/migrations
USER 10001:10001
ENTRYPOINT ["alembic"]
CMD ["upgrade", "head"]

# ---------------------------------------------------------------- llm-gateway (COMP-006)
FROM runtime AS gateway
EXPOSE 8200
HEALTHCHECK --interval=15s --timeout=3s --start-period=15s --retries=3 \
  CMD ["python", "-c", "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8200/healthz', timeout=2).status == 200 else 1)"]
CMD ["uvicorn", "tela_gateway.main:create_app", "--factory", "--host", "0.0.0.0", "--port", "8200"]

# ---------------------------------------------------------------- connector-runner (COMP-005)
FROM runtime AS runner
USER root
# Microsoft ODBC Driver 18 for SQL Server and the Dataverse TDS endpoint (US-110, US-114). Encrypt is on by
# default in Driver 18; the adapters never turn it off outside development.
RUN apt-get update \
    && apt-get install -y --no-install-recommends curl gnupg ca-certificates \
    && curl -fsSL https://packages.microsoft.com/keys/microsoft.asc \
       | gpg --dearmor -o /usr/share/keyrings/microsoft-prod.gpg \
    && echo "deb [arch=$(dpkg --print-architecture) signed-by=/usr/share/keyrings/microsoft-prod.gpg] https://packages.microsoft.com/debian/12/prod bookworm main" \
       > /etc/apt/sources.list.d/mssql-release.list \
    && apt-get update \
    && ACCEPT_EULA=Y apt-get install -y --no-install-recommends msodbcsql18 unixodbc libgssapi-krb5-2 \
    && apt-get purge -y curl gnupg && apt-get autoremove -y \
    && rm -rf /var/lib/apt/lists/*
# Uploaded files are copied to tmpfs for DuckDB (US-109); the runner has no other writable place.
ENV TELA_RUNNER_FILE_DIR=/tmp/tela-files
USER 10001:10001
EXPOSE 8100
HEALTHCHECK --interval=15s --timeout=3s --start-period=15s --retries=3 \
  CMD ["python", "-c", "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8100/healthz', timeout=2).status == 200 else 1)"]
CMD ["uvicorn", "tela_runner.main:create_app", "--factory", "--host", "0.0.0.0", "--port", "8100"]

# ---------------------------------------------------------------- conformance (US-117, local and CI only)
# The whole workspace with dev tools and ODBC Driver 18, for the connector conformance matrix and the
# dialect parity suite (scripts/conformance.sh). The repository is mounted at /src; never deployed.
FROM ${PYTHON_IMAGE} AS conformance
RUN apt-get update \
    && apt-get install -y --no-install-recommends curl gnupg ca-certificates \
    && curl -fsSL https://packages.microsoft.com/keys/microsoft.asc \
       | gpg --dearmor -o /usr/share/keyrings/microsoft-prod.gpg \
    && echo "deb [arch=$(dpkg --print-architecture) signed-by=/usr/share/keyrings/microsoft-prod.gpg] https://packages.microsoft.com/debian/12/prod bookworm main" \
       > /etc/apt/sources.list.d/mssql-release.list \
    && apt-get update \
    && ACCEPT_EULA=Y apt-get install -y --no-install-recommends msodbcsql18 unixodbc libgssapi-krb5-2 \
    && rm -rf /var/lib/apt/lists/*
COPY --from=ghcr.io/astral-sh/uv:0.9.7 /uv /usr/local/bin/uv
ENV UV_PROJECT_ENVIRONMENT=/opt/venv UV_LINK_MODE=copy UV_PYTHON_DOWNLOADS=never PATH="/opt/venv/bin:$PATH"
WORKDIR /src
COPY pyproject.toml uv.lock ./
COPY packages ./packages
COPY services ./services
COPY workers ./workers
RUN --mount=type=cache,target=/root/.cache/uv uv sync --frozen --python /usr/local/bin/python3
USER 10001:10001

# ---------------------------------------------------------------- worker / beat (COMP-008/009)
FROM runtime AS worker
# Headless Chromium for PDF export (US-096): Playwright's headless shell and its system libraries, installed as root
# into a shared path; the worker runs it as the non-root user with /tmp as its only writable place.
USER root
ENV PLAYWRIGHT_BROWSERS_PATH=/opt/ms-playwright HOME=/tmp XDG_CACHE_HOME=/tmp/.cache
RUN playwright install --with-deps --only-shell chromium \
    && rm -rf /var/lib/apt/lists/* \
    && chmod -R a+rX /opt/ms-playwright
USER 10001:10001
HEALTHCHECK --interval=30s --timeout=10s --start-period=30s --retries=3 \
  CMD ["celery", "-A", "tela_worker.celery_app", "inspect", "ping", "--timeout", "5"]
CMD ["celery", "-A", "tela_worker.celery_app", "worker", "--loglevel=INFO", \
     "-Q", "default,index,export,introspect,maintenance", "--concurrency", "2"]

FROM runtime AS beat
CMD ["celery", "-A", "tela_worker.celery_app", "beat", "--loglevel=INFO", "--schedule", "/tmp/celerybeat-schedule"]
