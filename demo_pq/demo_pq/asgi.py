"""ASGI configuration for the priority queue demo."""

import os

from django.core.asgi import get_asgi_application

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "demo_pq.settings")

application = get_asgi_application()
