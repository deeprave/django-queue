"""WSGI configuration for the async queue demo."""

import os

from django.core.wsgi import get_wsgi_application

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "demo_aq.settings")

application = get_wsgi_application()
