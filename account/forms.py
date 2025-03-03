from django import forms
from django.contrib.auth.forms import UserCreationForm
from django.contrib.auth.models import User

class RegistrationForm(UserCreationForm):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)

        self.fields['username'].widget.attrs.update({
            'class': 'form-control',
            'placeholder': 'Enter your username'
        })
        self.fields['email'].widget.attrs.update({
            'class': 'form-control',
            'placeholder': 'Enter your email'
        })
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
        self.fields['first_name'].widget.attrs.update({
            'class': 'form-control',
            'placeholder': 'Enter your first name'
        })
        self.fields['last_name'].widget.attrs.update({
            'class': 'form-control',
            'placeholder': 'Enter your last name'
        })
        self.fields['password1'].widget.attrs.update({
            'class': 'form-control',
            'placeholder': 'Enter your password'
        })
        self.fields['password2'].widget.attrs.update({
            'class': 'form-control',
            'placeholder': 'Confirm your password'
        })

    email = forms.EmailField(required=True)
    first_name = forms.CharField(max_length=30, required=True)
    last_name = forms.CharField(max_length=100, required=True)
    phone = forms.CharField(max_length=20, required=True)
    country = forms.CharField(max_length=20, required=True)
    city = forms.CharField(max_length=50, required=True)
    address = forms.CharField(max_length=40, required=True)
    postal_or_zip_code = forms.CharField(max_length=16, required=True)

    class Meta:  # define a metadata related to this class
        model = User
        fields = (
            'username',
            'email',
            'first_name',
            'last_name',
            'password1',
            'password2',
            'city',
            'address',
            'postal_or_zip_code',
            'phone',
        )

    def save(self, commit=True):
        user = super(RegistrationForm, self).save(commit=False)
        user.email = self.cleaned_data['email']
        user.first_name = self.cleaned_data['first_name']
        user.last_name = self.cleaned_data['last_name']
        user.phone = self.cleaned_data['phone']
        user.country = self.cleaned_data['country']
        user.city = self.cleaned_data['city']
        user.address = self.cleaned_data['address']
        user.postal_or_zip_code = self.cleaned_data['postal_or_zip_code']

        if commit:
            user.save()  # running sql in database to store data
        return user

