from django.core.exceptions import ValidationError
from django.db import models, transaction
from shortuuid.django_fields import ShortUUIDField
from account.models import Account
from creature.models import Creature

CARD_TYPE = (
    ("master", "master"),
    ("visa", "visa"),
)

EXCHANGE_STATUS = (
    ("failed", "failed"),
    ("completed", "completed"),
)

class TransferManager(models.Manager):
    def create_transaction(self, sender, receiver, amount):
        """
        Factory method
        """
        if amount <= 0:
            raise ValidationError("Amount must be higher than zero")
        if sender == receiver:
            raise ValidationError("Cannot transfer between same accounts")

        with transaction.atomic():
            if sender.balance_cents < amount:
                raise ValidationError("Insufficient funds", code='insufficient_funds')
            transfer = self.create(
                sender=sender, 
                receiver=receiver, 
                amount=amount, 
                description=f"Transferencia de {amount}",
                status="pending"
            )

            transfer.execute_transaction()
            
            return transfer

class Transfer(models.Model):
    objects = TransferManager()
    exchange_id = ShortUUIDField(unique=True, length=15, max_length=20, prefix="TRN")
    description = models.CharField(max_length=1000, null=True, blank=True)

    receiver = models.ForeignKey(Account, on_delete=models.SET_NULL, null=True, related_name="sent_transfers")
    sender = models.ForeignKey(Account, on_delete=models.SET_NULL, null=True, related_name="received_transfers")

    status = models.CharField(choices=EXCHANGE_STATUS ,max_length=100, default="pending")

    date = models.DateTimeField(auto_now_add=True)

    amount = models.BigIntegerField()

    def execute_transaction(self):
        """Execute the transaction between users"""
        from django.db import transaction as db_transaction
        
        try:
            with db_transaction.atomic():
                sender_acc = Account.objects.select_for_update().get(pk=self.sender.id)
                receiver_acc = Account.objects.select_for_update().get(pk=self.receiver.id)
                
                sender_acc.balance_cents -= self.amount
                receiver_acc.balance_cents += self.amount
                sender_acc.save(update_fields=['balance_cents'])
                receiver_acc.save(update_fields=['balance_cents'])

                self.status = "completed"
                self.save(update_fields=['status'])
        except Exception as e:
            if self.status != "failed":
                self.status = "failed"
                self.save(update_fields=['status'])
            raise e

    def __str__(self):
        return f"Transfer of {self.sender} to {self.receiver}"

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
