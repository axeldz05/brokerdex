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
