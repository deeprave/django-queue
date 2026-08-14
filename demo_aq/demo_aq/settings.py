"""Settings for the deliberately database-free async queue demo."""

import os
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent

SECRET_KEY = "demo-only-not-for-production"
DEBUG = True
ALLOWED_HOSTS = ["*"]

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
        "BACKEND": "django_queue.backends.RedisQueueJson",
        "LOCATION": os.environ.get("DEMO_REDIS_URL", "redis://127.0.0.1:16379/0"),
        "TIMEOUT": 300,
        "HANDLER": "dashboard.demo_worker.handle_demo_entry",
        "WORKER": "dashboard.demo_worker.DemoQueueWorker",
    }
}
