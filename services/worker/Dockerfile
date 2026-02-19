FROM python:3.11-slim

WORKDIR /app
ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1
ENV PYTHONPATH=/app:/shared

RUN pip install --no-cache-dir \
    celery redis sqlalchemy \
    psycopg[binary] requests \
    pydantic-settings python-dotenv \
    playwright beautifulsoup4 pyyaml lxml \
    structlog==24.1.0 python-json-logger==2.0.7

COPY app /app

CMD ["celery", "-A", "celery_app.celery", "worker", "--loglevel=INFO", "--concurrency=2"]
