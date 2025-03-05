from django import forms
from django.forms import ModelForm
from django.contrib.auth.models import User
from django.contrib.auth.forms import UserCreationForm
from .models import Account

class AccountForm(ModelForm):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)

        self.fields['phone'].widget.attrs.update({
            'class': 'form-control',
            'placeholder': 'Enter your phone number'
        })
        self.fields['country'].widget.attrs.update({
            'class': 'form-control',
            'placeholder': 'Enter your country'
        })
        self.fields['city'].widget.attrs.update({
            'class': 'form-control',
            'placeholder': 'Enter your city'
        })
        self.fields['address'].widget.attrs.update({
            'class': 'form-control',
            'placeholder': 'Enter your address'
        })
        self.fields['postal_or_zip_code'].widget.attrs.update({
            'class': 'form-control',
            'placeholder': 'Enter your postal/zip code'
        })

    phone = forms.CharField(max_length=20, required=True)
    country = forms.CharField(max_length=20, required=True)
    city = forms.CharField(max_length=50, required=True)
    address = forms.CharField(max_length=40, required=True)
    postal_or_zip_code = forms.CharField(max_length=16, required=True)

    class Meta:  # define a metadata related to this class
        model = Account
        fields = (
            'country',
            'city',
            'address',
            'postal_or_zip_code',
            'phone',
        )


class CustomUserForm(UserCreationForm):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)

        self.fields['first_name'].widget.attrs.update({
            'class': 'form-control',
            'placeholder': 'Enter your first name.'
        })
        self.fields['last_name'].widget.attrs.update({
            'class': 'form-control',
            'placeholder': 'Enter your last name.'
        })
        self.fields['username'].widget.attrs.update({
            'class': 'form-control',
            'placeholder': 'Enter your username.'
        })
        self.fields['email'].widget.attrs.update({
            'class': 'form-control',
            'placeholder': 'Enter your email.'
        })
        self.fields['password1'].widget.attrs.update({
            'class': 'form-control',
            'placeholder': 'Enter your password.'
        })
        self.fields['password2'].widget.attrs.update({
            'class': 'form-control',
            'placeholder': 'Confirm your password.'
        })

    class Meta:  # define a metadata related to this class
        model = User
        fields = (
            'username',
            'email',
            'first_name',
            'last_name',
            'password1',
            'password2',
        )

class GeneralSettingsForm(forms.ModelForm):
    class Meta:
        model = User
        fields = ['username', 'email', 'first_name', 'last_name']

    def __init__(self, *args, **kwargs):
        super(GeneralSettingsForm, self).__init__(*args, **kwargs)
        self.fields['username'].disabled = True
        self.fields['email'].disabled = True

        self.fields['username'].widget.attrs.update({'readonly': 'readonly'})
        self.fields['email'].widget.attrs.update({'readonly': 'readonly'})
