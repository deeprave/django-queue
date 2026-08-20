FROM python:3.14-slim

WORKDIR /app

COPY pyproject.toml README.md LICENSE.md ./
COPY django_queue ./django_queue
RUN pip install --no-cache-dir ".[redis]" "faker>=40.36.0"

COPY demo_pq ./demo_pq

WORKDIR /app/demo_pq
CMD ["python", "manage.py", "runserver", "0.0.0.0:8000"]
