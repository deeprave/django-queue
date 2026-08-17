"""Dashboard and SSE views for the event queue demo."""

from django.http import StreamingHttpResponse
from django.shortcuts import render
from django.views.decorators.http import require_GET

from .projection import projection


@require_GET
def index(request):
    """Render the listener-driven dashboard shell."""
    return render(request, "dashboard/index.html")


@require_GET
def events(request):
    """Stream listener projection snapshots to the dashboard."""
    response = StreamingHttpResponse(
        projection.events(), content_type="text/event-stream"
    )
    response["Cache-Control"] = "no-cache"
    response["X-Accel-Buffering"] = "no"
    return response
