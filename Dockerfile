FROM python:3.14-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app

# Непривилегированный пользователь
RUN useradd --create-home --uid 10001 appuser

COPY requirements.txt .
RUN pip install -r requirements.txt

COPY app ./app

# Каталог для SQLite (монтируется томом в docker-compose)
RUN mkdir -p /app/data && chown -R appuser:appuser /app

USER appuser

CMD ["python", "-m", "app.main"]
