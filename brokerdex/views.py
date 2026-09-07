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
