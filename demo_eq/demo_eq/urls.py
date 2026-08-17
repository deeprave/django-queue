"""URL configuration for the event queue demo."""

from django.urls import include, path

urlpatterns = [path("", include("dashboard.urls"))]
