FROM python:3.14-slim

WORKDIR /app

COPY pyproject.toml README.md LICENSE.md ./
COPY django_queue ./django_queue
RUN pip install --no-cache-dir ".[redis]"

COPY demo_eq ./demo_eq

WORKDIR /app/demo_eq
CMD ["python", "manage.py", "runserver", "0.0.0.0:8000"]
