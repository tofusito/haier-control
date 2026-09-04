FROM python:3.13-slim AS builder

ENV PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_NO_CACHE_DIR=1
WORKDIR /build
COPY pyproject.toml README.md ./
COPY app ./app
RUN python -m pip wheel --wheel-dir /wheels .

FROM python:3.13-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    HAIER_DATABASE_PATH=/data/haier-control.db \
    HAIER_ENCRYPTED_SESSION_FILE=/data/haier-session.enc \
    HAIER_MASTER_KEY_FILE=/run/secrets/haier_control_master_key \
    HAIER_BIND_HOST=0.0.0.0 \
    HAIER_PORT=8787

RUN groupadd --gid 1000 app && useradd --uid 1000 --gid app --create-home app
COPY --from=builder /wheels /wheels
RUN python -m pip install --no-cache-dir /wheels/* && rm -rf /wheels
WORKDIR /app
USER 1000:1000
EXPOSE 8787
HEALTHCHECK --interval=15s --timeout=4s --start-period=12s --retries=4 \
  CMD ["python", "-c", "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8787/healthz', timeout=3).read()"]
ENTRYPOINT ["haier-control"]
CMD ["serve"]
