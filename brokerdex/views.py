from django.http import HttpResponse, JsonResponse
from django.shortcuts import render, redirect
from django.views.decorators.cache import cache_control

from .observability import readiness_status


def health_live(request):
    return JsonResponse({"status": "ok"})


def health_ready(request):
    status, checks = readiness_status()
    status_code = 200 if status == "ok" else 503
    return JsonResponse(
        {"status": status, "checks": checks},
        status=status_code,
    )


def index(request):
    if request.user.is_authenticated:
        return redirect('dashboard:dashboard')
    else:
        return render(request, 'index.html', {})


@cache_control(max_age=3600)
def sw_js(request):
    return HttpResponse(
        "self.addEventListener('install',()=>self.skipWaiting());"
        "self.addEventListener('activate',()=>clients.claim());"
        "self.addEventListener('fetch',(e)=>{})",
        content_type='application/javascript',
    )


def ratelimited_view(request, exception=None):
    """
    Handle HTTP 429 Too Many Requests when rate limits are exceeded.
    Returns JSON for API/webhook requests and an HTML page for browser requests.
    """
    accept_header = request.headers.get("accept", "")
    content_type = getattr(request, "content_type", "")
    is_json = (
        request.path.startswith("/webhooks/")
        or request.path.startswith("/api/")
        or "application/json" in accept_header
        or "application/json" in content_type
    )

    if is_json:
        response = JsonResponse(
            {
                "status": "error",
                "code": "rate_limited",
                "message": "Too many requests. Please try again later.",
            },
            status=429,
        )
    else:
        response = render(request, "429.html", status=429)

    response["Retry-After"] = "60"
    return response

