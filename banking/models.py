from django.db import models
from django.contrib.auth.models import User

class UserExtension(models.Model):
    user = models.OneToOneField(User, on_delete=models.CASCADE)
    current_balance = models.IntegerField(default=0)

    def __str__(self):
        return self.user.first_name + " " + self.user.last_name
