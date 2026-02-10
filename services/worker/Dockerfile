FROM python:3.11-slim

WORKDIR /app
ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1

RUN pip install --no-cache-dir celery redis sqlalchemy psycopg[binary] requests pydantic-settings python-dotenv playwright beautifulsoup4 pyyaml lxml

# add scraping libs only if needed (keep lean)
# RUN pip install --no-cache-dir playwright && playwright install --with-deps chromium

COPY app /app

CMD ["celery", "-A", "celery_app.celery", "worker", "--loglevel=INFO", "--concurrency=2"]
