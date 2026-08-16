"""Settings for the deliberately database-free async queue demo."""

import os
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent

SECRET_KEY = os.environ.get("DEMO_SECRET_KEY", "demo-only-not-for-production")
DEBUG = os.environ.get("DEMO_DEBUG", "true").lower() in {"1", "true", "yes", "on"}
ALLOWED_HOSTS = [
    host.strip()
    for host in os.environ.get("DEMO_ALLOWED_HOSTS", "localhost,127.0.0.1").split(",")
    if host.strip()
]

INSTALLED_APPS = [
    "django.contrib.staticfiles",
    "django_queue.apps.DjangoQueueConfig",
    "dashboard.apps.DashboardConfig",
]

MIDDLEWARE: list[str] = []
ROOT_URLCONF = "demo_aq.urls"
WSGI_APPLICATION = "demo_aq.wsgi.application"
ASGI_APPLICATION = "demo_aq.asgi.application"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [],
        "APP_DIRS": True,
        "OPTIONS": {"context_processors": []},
    }
]

STATIC_URL = "static/"
USE_TZ = True

QUEUES = {
    "demo": {
        "BACKEND": "django_queue.backends.redis.RedisAsyncQueueJson",
        "LOCATION": os.environ.get("DEMO_REDIS_URL", "redis://127.0.0.1:16379/0"),
        "TIMEOUT": 300,
        "RETENTION_TIMEOUT": 30,
        "HANDLER": "dashboard.demo_worker.handle_demo_entry",
        "WORKER": "dashboard.demo_worker.DemoQueueWorker",
    }
}
