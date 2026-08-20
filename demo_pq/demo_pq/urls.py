"""URL configuration for the priority queue demo."""

from django.urls import include, path

urlpatterns = [path("", include("dashboard.urls"))]
