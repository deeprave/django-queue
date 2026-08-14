from django.http import StreamingHttpResponse
from django.shortcuts import redirect, render
from django.views.decorators.http import require_GET, require_POST

from .projection import projection


def index(request):
    """Render the observer-backed dashboard table shell."""
    projection.start()
    return render(request, "dashboard/index.html")


@require_GET
def events(request):
    """Stream observer projection updates to the dashboard."""
    projection.start()
    response = StreamingHttpResponse(
        projection.events(), content_type="text/event-stream"
    )
    response["Cache-Control"] = "no-cache"
    response["X-Accel-Buffering"] = "no"
    return response


@require_POST
def refresh(request):
    """Replace the observer projection before reloading the dashboard."""
    projection.refresh()
    return redirect("index")
