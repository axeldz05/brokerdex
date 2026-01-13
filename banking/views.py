from django.core.exceptions import ObjectDoesNotExist, ValidationError
from django.shortcuts import render, redirect
from django.contrib.auth.decorators import login_required
from django.contrib import messages

from account.models import Account
from creature.models import Creature
from .forms import TransferForm

@login_required

def transfer(request):
    if request.method == 'POST':
        form = TransferForm(request.POST)
        
        if form.is_valid():
            amount = form.cleaned_data['amount']
            receiver_username = form.cleaned_data['receiver_username']
            
            try:
                from .models import Transfer

                receiver_acc = Account.objects.get(username=receiver_username)
                Transfer.objects.create_transaction(
                    sender=request.user,
                    receiver=receiver_acc,
                    amount=amount
                )
                messages.success(request, f"Transaction of ${amount} realized succesfully!")
                return redirect('dashboard')
            
            except ValidationError as e:
                messages.error(request, e.message)
            
            except Exception as e:
                messages.error(request, f"Error: {str(e)}")
        else:
            for field, errors in form.errors.items():
                for error in errors:
                    messages.error(request, f"{field}: {error}")

    else:
        form = TransferForm()

    return render(request, 'transfer.html', {'form': form})

@login_required
def withdraw(request):
    return render(request,'withdraw.html',{})

@login_required
def invest(request):
    return render(request, 'invest.html', {
        'creatures': Creature.objects.all()
    })


@login_required
def deposit(request):
    return render(request,'deposit.html',{})
