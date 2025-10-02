from django.db import models
from django.contrib.auth.models import AbstractBaseUser, PermissionsMixin
from django.utils.translation import gettext_lazy as _
from .managers import AccountManager

class Account(AbstractBaseUser, PermissionsMixin):
    email = models.EmailField(_('email address'), unique=True)
    
    phone = models.CharField(max_length=20, blank=True)
    country = models.CharField(max_length=20, blank=True)
    city = models.CharField(max_length=50, blank=True)
    address = models.CharField(max_length=40, blank=True)
    postal_or_zip_code = models.CharField(max_length=16, blank=True)

    balance_cents = models.BigIntegerField(default=0)

    # For Django's admin and auth system
    is_active = models.BooleanField(default=True)
    is_staff = models.BooleanField(default=False)

    date_joined = models.DateTimeField(auto_now_add=True)

    USERNAME_FIELD = 'email'
    objects = AccountManager()

    def __str__(self):
        return self.email

    @property
    def balance_dollars(self):
        return self.balance_cents / 100.0

    @balance_dollars.setter
    def balance_dollars(self, value):
        self.balance_cents = int(round(value * 100))
