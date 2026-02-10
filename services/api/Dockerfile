FROM python:3.11-slim

WORKDIR /app
ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1

RUN pip install --no-cache-dir \
  fastapi uvicorn[standard] \
  sqlalchemy psycopg[binary] \
  pydantic-settings \
  jinja2 python-multipart \
  itsdangerous \
  celery redis

COPY app /app

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
