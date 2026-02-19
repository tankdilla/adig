FROM python:3.11-slim

WORKDIR /app
ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1
ENV PYTHONPATH=/app:/shared

RUN pip install --no-cache-dir \
  fastapi uvicorn[standard] \
  sqlalchemy psycopg[binary] \
  pydantic-settings \
  jinja2 python-multipart \
  itsdangerous \
  celery redis \
  structlog==24.1.0 python-json-logger==2.0.7

COPY app /app

CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000"]
