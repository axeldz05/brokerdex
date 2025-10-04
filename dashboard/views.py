from django.contrib.auth.views import login_required
from django.shortcuts import render

@login_required
def dashboard(request):
    return render(request, 'dashboard/dashboard.html')
