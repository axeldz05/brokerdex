from django.contrib.auth.views import login_required
from django.shortcuts import render

@login_required
def dashboard(request):
    account = request.user
    transactions = account.get_transaction_history()
    return render(request, 'dashboard/dashboard.html', {'transactions': transactions})
