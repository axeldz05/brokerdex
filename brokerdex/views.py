from django.conf import settings
from django.http import Http404, HttpResponse, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
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


def dev_login_as(request, username):
    """
    Developer convenience endpoint to impersonate any user without credentials.
    STRICTLY enabled only when settings.DEBUG is True.
    """
    if not settings.DEBUG:
        raise Http404("Developer login is only available in DEBUG mode.")

    from account.models import Account
    from django.contrib.auth import login

    user = get_object_or_404(Account, username=username)
    login(request, user, backend='account.backends.EmailOrUsernameModelBackend')
    next_url = request.GET.get('next', 'dashboard:dashboard')
    return redirect(next_url)


def dev_login_list(request):
    """
    Developer convenience UI listing all simulation bots and demo accounts
    with 1-click login buttons. STRICTLY enabled only when settings.DEBUG is True.
    """
    if not settings.DEBUG:
        raise Http404("Developer login is only available in DEBUG mode.")

    from account.models import Account

    bots = Account.objects.filter(username__startswith='sim_').order_by('username')
    demo_users = Account.objects.filter(username__startswith='trainer_').order_by('username')

    return render(request, 'dev_login.html', {
        'bots': bots,
        'demo_users': demo_users,
    })


