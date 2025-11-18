from django.db import models
from shortuuid.django_fields import ShortUUIDField
from account.models import Account
from creature.models import Creature

CARD_TYPE = (
    ("master", "master"),
    ("visa", "visa"),
)

EXCHANGE_TYPE = (
    ("exchange", "Exchange"),
    ("none", "None")
)

EXCHANGE_STATUS = (
    ("failed", "failed"),
    ("completed", "completed"),
    ("pending", "pending"),
    ("processing", "processing"),
    ("request_sent", "request_sent"),
    ("request_settled", "request settled"),
    ("request_processing", "request processing"),
)

class Exchange(models.Model):
    exchange_id = ShortUUIDField(unique=True, length=15, max_length=20, prefix="TRN")
    user = models.ForeignKey(Account, on_delete=models.SET_NULL, null=True, related_name="user")
    pokemon = models.ForeignKey(Creature, on_delete=models.SET_NULL, null=True, related_name="creature")
    description = models.CharField(max_length=1000, null=True, blank=True)

    receiver = models.ForeignKey(Account, on_delete=models.SET_NULL, null=True, related_name="receiver")
    sender = models.ForeignKey(Account, on_delete=models.SET_NULL, null=True, related_name="sender")

    receiver_account = models.ForeignKey(Account, on_delete=models.SET_NULL, null=True, related_name="receiver_account")
    sender_account = models.ForeignKey(Account, on_delete=models.SET_NULL, null=True, related_name="sender_account")

    status = models.CharField(choices=EXCHANGE_STATUS ,max_length=100, default="pending")
    exchange_type = models.CharField(choices=EXCHANGE_TYPE ,max_length=100, default="none")

    date = models.DateTimeField(auto_now_add=True)
    update = models.DateTimeField(auto_now_add=False, null=True, blank=True)

    def __str__(self):
        return f"{self.user}" or "Exchange with no user attached"

class CreditCard(models.Model):
    user = models.ForeignKey(Account, on_delete=models.CASCADE)
    card_id = ShortUUIDField(unique=True, length=5, max_length=20, prefix="CARD", alphabet="1234567890")

    name = models.CharField(max_length=100)
    number = models.IntegerField()
    month = models.IntegerField()
    year = models.IntegerField()
    cvv = models.IntegerField()

    amount = models.DecimalField(max_digits=12, decimal_places=2, default=0.00)

    card_type = models.CharField(choices=CARD_TYPE, max_length=20, default="master")
    card_status = models.BooleanField()

    date = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"{self.user}"
