from django.http import HttpResponse
from django.shortcuts import render, redirect
from django.views.decorators.cache import cache_control


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
