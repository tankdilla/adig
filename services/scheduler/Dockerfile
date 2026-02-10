FROM python:3.11-slim

WORKDIR /app
RUN pip install --no-cache-dir celery redis

COPY app /app

CMD ["celery", "-A", "beat.celery", "beat", "--loglevel=INFO"]
