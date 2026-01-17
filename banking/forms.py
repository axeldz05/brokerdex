from django import forms

class TransferForm(forms.Form):
    receiver_username = forms.CharField(label="Recipient", max_length=150)
    amount = forms.IntegerField(label="Funds", min_value=1)

    def clean_receiver_username(self):
        from account.models import Account
        username = self.cleaned_data['receiver_username']
        if not Account.objects.filter(username=username).exists():
            raise forms.ValidationError(u'Username "%s" does not exist.' % username)
        return username

class WithdrawForm(forms.Form):
    target_identifier = forms.CharField(label="Destination", max_length=100)
    amount = forms.DecimalField(label="Amount", max_digits=12, decimal_places=2)

    def __init__(self, *args, **kwargs):
        self.user_account = kwargs.pop('account', None)
        super().__init__(*args, **kwargs)

    def clean_amount(self):
        amount = self.cleaned_data['amount']
        if self.user_account and self.user_account.balance < amount:
             raise forms.ValidationError("Fondos insuficientes.")
        return amount
