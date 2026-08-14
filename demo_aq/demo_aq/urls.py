"""URL configuration for the async queue demo."""

from django.urls import include, path

urlpatterns = [path("", include("dashboard.urls"))]
