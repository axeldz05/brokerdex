from django.db import models
from django.contrib.auth.models import User

class Account(models.Model):
    user = models.OneToOneField(User, on_delete=models.CASCADE)
    current_balance = models.IntegerField(default=0)

    phone = models.CharField(max_length=20, default=None, blank=True)
    country = models.CharField(max_length=20, default=None, blank=True)
    city = models.CharField(max_length=50, default=None, blank=True)
    address = models.CharField(max_length=40, default=None, blank=True)
    postal_or_zip_code = models.CharField(max_length=16, default=None, blank=True)

    def __str__(self):
        return self.user.get_full_name() or self.user.username
