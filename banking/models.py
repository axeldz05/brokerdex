from django.db import models, transaction
from shortuuid.django_fields import ShortUUIDField
from django.core.exceptions import ValidationError
from account.models import Account
from creature.models import Creature
from .services import ExternalBankService

class TransferManager(models.Manager):
    def process_external_deposit(self, target_identifier, amount, identifier_type='CVU'):
        if amount <= 0:
            raise ValidationError("Amount must be higher than zero")
        try:
            if identifier_type == 'CVU':
                receiver = Account.objects.get(cvu=target_identifier)
            elif identifier_type == 'ALIAS':
                receiver = Account.objects.get(alias=target_identifier)
            else:
                raise ValidationError("Type of identifier not supported")
        except Account.DoesNotExist:
            raise ValidationError("Receiver account not found")

        with transaction.atomic():
            receiver = Account.objects.select_for_update().get(pk=receiver.pk)
            transfer = self.create(
                sender=None, 
                type=self.model.TransactionType.DEPOSIT,
                receiver=receiver,
                amount=amount,
                status=Transfer.TransactionStatus.PENDING,
                description=f"External Deposit via {identifier_type}"
            )
            
            receiver.balance_cents += amount
            receiver.save(update_fields=['balance_cents'])
            
            transfer.status = 'completed'
            transfer.save(update_fields=['status'])
            
            return transfer
    def process_external_withdrawal(self, sender, target_cvu, amount):
        if amount <= 0: raise ValidationError("Monto positivo requerido")
        bank_service = ExternalBankService()

        with transaction.atomic():
            sender_locked = Account.objects.select_for_update().get(pk=sender.pk)
            if sender_locked.balance_cents < amount:
                raise ValidationError("Insufficient Funds")
            transfer = self.create(
                sender=sender_locked,
                receiver=None, 
                type=self.model.TransactionType.WITHDRAW,
                amount=amount,
                status=Transfer.TransactionStatus.PENDING,
                description=f"Withdraw to CVU {target_cvu}"
            )
            success = bank_service.send_money(
                source_cvu=sender_locked.cvu,
                target_cvu=target_cvu,
                amount=amount
            )
            if success:
                sender_locked.balance_cents -= amount
                sender_locked.save(update_fields=['balance_cents'])
                transfer.status = 'completed'
                transfer.save(update_fields=['status'])
                return transfer
            else:
                transfer.status = 'failed'
                transfer.save(update_fields=['status'])
                raise ValidationError("The bank rejected the transaction.")

    def create_transaction(self, sender, receiver, amount, description=""):
        if amount <= 0:
            raise ValidationError("Amount must be higher than zero")
        if sender == receiver:
            raise ValidationError("Cannot transfer between same accounts")

        with transaction.atomic():
            if sender.balance_cents < amount:
                raise ValidationError("Insufficient funds", code='insufficient_funds')
            transfer = self.create(
                sender=sender, 
                type=Transfer.TransactionType.TRANSFER,
                receiver=receiver, 
                amount=amount, 
                description=description,
                status=Transfer.TransactionStatus.PENDING
            )

            transfer.execute_transaction()
            
            return transfer

class Transfer(models.Model):
    class TransactionType(models.TextChoices):
        TRANSFER = 'TRANSFER', 'Transfer between users'
        DEPOSIT = 'DEPOSIT', 'External deposit'
        WITHDRAW = 'WITHDRAW', 'External withdraw'
    class TransactionStatus(models.TextChoices):
        FAILED = 'failed', 'Transaction rejected'
        PENDING = 'pending', 'The transaction is being processed'
        COMPLETED = 'completed', 'Transaction completed'

    objects = TransferManager()
    exchange_id = ShortUUIDField(unique=True, length=15, max_length=20, prefix="TRN")
    type = models.CharField(
        max_length=20,
        choices=TransactionType.choices,
    )
    status = models.CharField(
        max_length=20,
        choices=TransactionStatus.choices,
        default=TransactionStatus.PENDING
    )
    description = models.CharField(max_length=1000, null=True, blank=True)

    receiver = models.ForeignKey(Account, on_delete=models.SET_NULL, null=True, related_name="sent_transfers")
    sender = models.ForeignKey(Account, on_delete=models.SET_NULL, null=True, related_name="received_transfers")

    date = models.DateTimeField(auto_now_add=True)

    amount = models.BigIntegerField()

    def execute_transaction(self):
        """Execute the transaction between users"""
        try:
            with transaction.atomic():
                sender_acc = Account.objects.select_for_update().get(pk=self.sender.id)
                receiver_acc = Account.objects.select_for_update().get(pk=self.receiver.id)
                
                sender_acc.balance_cents -= self.amount
                receiver_acc.balance_cents += self.amount
                sender_acc.save(update_fields=['balance_cents'])
                receiver_acc.save(update_fields=['balance_cents'])

                self.status = Transfer.TransactionStatus.COMPLETED
                self.save(update_fields=['status'])
        except Exception as e:
            if self.status != Transfer.TransactionStatus.FAILED:
                self.status = Transfer.TransactionStatus.FAILED
                self.save(update_fields=['status'])
            raise e

    def __str__(self):
        return f"Transfer of {self.sender} to {self.receiver}"
