FROM python:3.14-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app

# Unprivileged user
RUN useradd --create-home --uid 10001 appuser

# Install the package (pyproject is the single source of truth for deps).
COPY pyproject.toml README.md ./
COPY antispam_bot ./antispam_bot
RUN pip install .

# Data directory for SQLite (mounted as a volume in docker-compose).
RUN mkdir -p /app/data && chown -R appuser:appuser /app

USER appuser

CMD ["antispam-bot"]
