from django.core.exceptions import ObjectDoesNotExist
from django.shortcuts import render, redirect
from django.contrib.auth.decorators import login_required
from django.contrib import messages

from account.models import Account
from .forms import RecipientForm, AmountForm

@login_required
def transfer(request):
    user_balance = request.user.balance_dollars
    
    if request.method == 'POST':
        if 'verify_recipient' in request.POST:
            recipient_form = RecipientForm(request.POST)
            if recipient_form.is_valid():
                recipient_username = recipient_form.cleaned_data['recipient_username']
                try:
                    recipient = Account.objects.get(username=recipient_username)
                    if recipient == request.user:
                        messages.error(request, "You cannot transfer to yourself.")
                    else:
                        return render(request, 'transfer.html', {
                            'user_balance': user_balance,
                            'show_amount': True,
                            'recipient_username': recipient_username,
                            'amount_form': AmountForm()
                        })
                except ObjectDoesNotExist:
                    messages.error(request, "Recipient username not found.")
        
        elif 'transfer' in request.POST:
            amount_form = AmountForm(request.POST)
            if amount_form.is_valid():
                amount = amount_form.cleaned_data['amount']
                recipient_username = request.POST.get('recipient_username')
                
                if amount > user_balance:
                    messages.error(request, "Insufficient funds.")
                else:
                    # TODO: transfer logic should occur here
                    messages.success(request, f"Successfully transferred ${amount} to {recipient_username}")
                    return redirect('dashboard')
    
    return render(request, 'transfer.html', {
        'user_balance': user_balance,
        'recipient_form': RecipientForm(),
        'show_amount': False
    })

@login_required
def withdraw(request):
    return render(request,'banking/withdraw.html',{})

@login_required
def invest(request):
    return render(request,'banking/invest.html',{})

@login_required
def deposit(request):
    return render(request,'banking/deposit.html',{})
