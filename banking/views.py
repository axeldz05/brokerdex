import hashlib
import hmac
import json
import logging

from django.conf import settings
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import ValidationError
from django.http import JsonResponse
from django.shortcuts import redirect, render
from django.views.decorators.csrf import csrf_exempt
from django_ratelimit.decorators import ratelimit

from account.money import cents_to_dollars, dollars_to_cents
from account.models import Account
from creature.models import Creature

from .forms import TransferForm, WithdrawForm
from .models import Transfer
from .services import DepositService


logger = logging.getLogger(__name__)


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
@ratelimit(key="user", rate="10/m", method="POST", block=True)
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
@ratelimit(key="user", rate="10/m", method="POST", block=True)
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


@csrf_exempt
@ratelimit(key="ip", rate="60/m", method="POST", block=True)
def deposit_webhook(request):
    if request.method != "POST":
        return JsonResponse(
            {"status": "error", "code": "method_not_allowed"},
            status=405,
        )

    secret = getattr(settings, "DEPOSIT_WEBHOOK_SECRET", "")
    provided_signature = request.headers.get("X-Webhook-Signature", "")
    if provided_signature.startswith("sha256="):
        provided_signature = provided_signature.removeprefix("sha256=")
    expected_signature = hmac.new(
        str(secret).encode(),
        request.body,
        hashlib.sha256,
    ).hexdigest()
    if (
        not secret
        or not provided_signature
        or not hmac.compare_digest(provided_signature, expected_signature)
    ):
        return JsonResponse(
            {"status": "error", "code": "invalid_signature"},
            status=401,
        )

    try:
        payload = json.loads(request.body)
        if not isinstance(payload, dict):
            raise ValueError
        if payload.get("status") != "COMPLETED":
            raise ValueError
        DepositService.credit_completed(
            provider_reference=payload.get("provider_reference"),
            target_identifier=payload.get("target_identifier"),
            identifier_type=payload.get("identifier_type"),
            amount_cents=payload.get("amount_cents"),
        )
    except (json.JSONDecodeError, UnicodeDecodeError, ValueError):
        return JsonResponse(
            {"status": "error", "code": "invalid_payload"},
            status=400,
        )
    except ValidationError as error:
        code = error.error_list[0].code
        if code == "account_not_found":
            return JsonResponse(
                {"status": "error", "code": code},
                status=404,
            )
        return JsonResponse(
            {"status": "error", "code": "invalid_payload"},
            status=400,
        )
    except Exception:
        logger.exception("Deposit webhook processing failed.")
        return JsonResponse(
            {"status": "error", "code": "internal_error"},
            status=500,
        )

    return JsonResponse({"status": "ok"})
