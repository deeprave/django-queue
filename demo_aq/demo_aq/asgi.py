"""ASGI configuration for the async queue demo."""

import os

from django.core.asgi import get_asgi_application

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "demo_aq.settings")

application = get_asgi_application()
