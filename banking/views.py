from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import ValidationError
from django.shortcuts import redirect, render

from account.money import cents_to_dollars, dollars_to_cents
from account.models import Account
from creature.models import Creature

from .forms import TransferForm, WithdrawForm
from .models import Transfer


def _balance_context(account):
    balance_dollars = cents_to_dollars(account.balance_cents)
    return {
        "user_balance": balance_dollars,
        "user_balance_float": balance_dollars,
    }


def _message_form_errors(request, form):
    for field, errors in form.errors.items():
        for error in errors:
            messages.error(request, f"{field}: {error}")


@login_required
def transfer(request):
    if request.method == 'POST':
        form = TransferForm(request.POST)

        if form.is_valid():
            amount_dollars = form.cleaned_data['amount']
            receiver_username = form.cleaned_data['receiver_username']

            try:
                receiver_acc = Account.objects.get(username=receiver_username)
                Transfer.objects.create_transaction(
                    sender=request.user,
                    receiver=receiver_acc,
                    amount=dollars_to_cents(amount_dollars),
                )
                messages.success(
                    request,
                    f"Transaction of ${amount_dollars:.2f} "
                    "completed successfully.",
                )
                return redirect('dashboard:dashboard')

            except ValidationError as e:
                messages.error(request, e.message)

            except Exception as e:
                messages.error(request, f"Error: {str(e)}")
        else:
            _message_form_errors(request, form)

    else:
        form = TransferForm()

    context = {'form': form}
    context.update(_balance_context(request.user))
    return render(request, 'transfer.html', context)


@login_required
def withdraw(request):
    account = request.user
    if request.method == 'POST':
        form = WithdrawForm(request.POST, account=account)
        if form.is_valid():
            amount_dollars = form.cleaned_data['amount']
            try:
                transfer = Transfer.objects.process_external_withdrawal(
                    sender=account,
                    target_cvu=form.cleaned_data['target_identifier'],
                    amount=dollars_to_cents(amount_dollars),
                )
                if transfer.status == Transfer.TransactionStatus.COMPLETED:
                    messages.success(
                        request,
                        f"Withdrawal of ${amount_dollars:.2f} "
                        "completed successfully.",
                    )
                else:
                    messages.info(
                        request,
                        f"Withdrawal of ${amount_dollars:.2f} is pending "
                        "provider confirmation.",
                    )
                return redirect('dashboard:dashboard')
            except ValidationError as e:
                messages.error(request, e.message)
        else:
            _message_form_errors(request, form)
    else:
        form = WithdrawForm(account=account)

    context = {'form': form}
    context.update(_balance_context(account))
    return render(request, 'withdraw.html', context)


@login_required
def invest(request):
    return render(request, 'invest.html', {
        'creatures': Creature.objects.all()
    })


@login_required
def deposit(request):
    return render(request, 'deposit.html', {})
