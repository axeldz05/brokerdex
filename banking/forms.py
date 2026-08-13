from decimal import Decimal

from django import forms

from account.money import cents_to_dollars


class TransferForm(forms.Form):
    receiver_username = forms.CharField(label="Recipient", max_length=150)
    amount = forms.DecimalField(
        label="Amount",
        max_digits=12,
        decimal_places=2,
        min_value=Decimal("0.01"),
    )

    def clean_receiver_username(self):
        from account.models import Account
        username = self.cleaned_data['receiver_username']
        if not Account.objects.filter(username=username).exists():
            raise forms.ValidationError(
                u'Username "%s" does not exist.' %
                username)
        return username


class WithdrawForm(forms.Form):
    target_identifier = forms.CharField(label="Destination", max_length=100)
    amount = forms.DecimalField(
        label="Amount",
        max_digits=12,
        decimal_places=2,
        min_value=Decimal("0.01"),
    )

    def __init__(self, *args, **kwargs):
        self.user_account = kwargs.pop('account', None)
        super().__init__(*args, **kwargs)

    def clean_amount(self):
        amount = self.cleaned_data['amount']
        if self.user_account:
            balance_dollars = cents_to_dollars(
                self.user_account.balance_cents
            )
            if balance_dollars < amount:
                raise forms.ValidationError("Insufficient funds.")
        return amount
