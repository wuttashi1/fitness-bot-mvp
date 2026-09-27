# Build on the server's native architecture (64-bit amd64 or arm64).
FROM python:3.13-slim-bookworm AS builder
ENV PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_NO_CACHE_DIR=1
WORKDIR /build
RUN python -m venv /opt/venv
COPY requirements.lock ./
RUN /opt/venv/bin/python -m pip install -r requirements.lock
COPY pyproject.toml ./
COPY fitness_bot ./fitness_bot
RUN /opt/venv/bin/python -m pip install --no-deps .

FROM python:3.13-slim-bookworm AS runtime
ENV PATH="/opt/venv/bin:${PATH}" \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    DATABASE_PATH=/data/fitness.sqlite3
RUN groupadd --gid 10001 fitness \
    && useradd --uid 10001 --gid fitness --no-create-home --shell /usr/sbin/nologin fitness \
    && mkdir -p /app /data /backups \
    && chown fitness:fitness /data /backups
WORKDIR /app
COPY --from=builder /opt/venv /opt/venv
COPY scripts/backup.py /app/scripts/backup.py
USER 10001:10001
STOPSIGNAL SIGTERM
ENTRYPOINT ["python", "-m", "fitness_bot"]

