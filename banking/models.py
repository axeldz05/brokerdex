from uuid import uuid4

from django.core.exceptions import ValidationError
from django.db import models, transaction
from shortuuid.django_fields import ShortUUIDField

from account.money import cents_to_dollars
from account.models import Account


class TransferManager(models.Manager):
    def process_external_deposit(
            self, target_identifier, amount, identifier_type='CVU'):
        from .services import DepositService

        return DepositService.credit_completed(
            provider_reference=f"manual:{uuid4()}",
            target_identifier=target_identifier,
            identifier_type=identifier_type,
            amount_cents=amount,
        )

    def process_external_withdrawal(self, sender, target_cvu, amount):
        from .services import WithdrawalService

        return WithdrawalService.initiate(
            sender=sender,
            target_identifier=target_cvu,
            amount_cents=amount,
        )

    def create_transaction(self, sender, receiver, amount, description=""):
        if amount <= 0:
            raise ValidationError("Amount must be higher than zero")
        if sender.pk == receiver.pk:
            raise ValidationError("Cannot transfer between same accounts")

        with transaction.atomic():
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
        PROCESSING = 'processing', 'Submitted to external provider'
        COMPLETED = 'completed', 'Transaction completed'

    objects = TransferManager()
    exchange_id = ShortUUIDField(
        unique=True,
        length=15,
        max_length=20,
        prefix="TRN")
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
    source_identifier = models.CharField(max_length=100, blank=True)
    target_identifier = models.CharField(max_length=100, blank=True)
    provider_reference = models.CharField(
        max_length=255,
        null=True,
        blank=True,
    )
    provider_attempts = models.PositiveIntegerField(default=0)
    provider_error = models.TextField(blank=True)
    processing_started_at = models.DateTimeField(null=True, blank=True)
    processed_at = models.DateTimeField(null=True, blank=True)

    receiver = models.ForeignKey(
        Account,
        on_delete=models.SET_NULL,
        null=True,
        related_name="received_transfers",
    )
    sender = models.ForeignKey(
        Account,
        on_delete=models.SET_NULL,
        null=True,
        related_name="sent_transfers",
    )

    date = models.DateTimeField(auto_now_add=True)

    amount = models.BigIntegerField()

    class Meta:
        indexes = [
            models.Index(fields=['type', 'status']),
        ]
        constraints = [
            models.UniqueConstraint(
                fields=['provider_reference'],
                condition=models.Q(
                    type='DEPOSIT',
                    provider_reference__isnull=False,
                ),
                name='unique_deposit_provider_reference',
            ),
            models.CheckConstraint(
                condition=models.Q(amount__gt=0),
                name='transfer_amount_positive',
            ),
        ]

    @property
    def amount_dollars(self):
        return cents_to_dollars(self.amount)

    def execute_transaction(self):
        """Execute the transaction between users"""
        try:
            with transaction.atomic():
                transfer = Transfer.objects.select_for_update().get(pk=self.pk)
                if transfer.status == Transfer.TransactionStatus.COMPLETED:
                    self.status = transfer.status
                    return transfer
                if transfer.status == Transfer.TransactionStatus.FAILED:
                    raise ValidationError(
                        "Failed transfers cannot be executed."
                    )

                locked_accounts = {
                    account.pk: account
                    for account in Account.objects.select_for_update().filter(
                        pk__in=[transfer.sender_id, transfer.receiver_id]
                    ).order_by("pk")
                }
                sender_acc = locked_accounts[transfer.sender_id]
                receiver_acc = locked_accounts[transfer.receiver_id]

                if sender_acc.balance_cents < transfer.amount:
                    raise ValidationError(
                        "Insufficient funds",
                        code="insufficient_funds",
                    )

                sender_acc.balance_cents -= transfer.amount
                receiver_acc.balance_cents += transfer.amount
                sender_acc.save(update_fields=['balance_cents'])
                receiver_acc.save(update_fields=['balance_cents'])

                transfer.status = Transfer.TransactionStatus.COMPLETED
                transfer.save(update_fields=['status'])
                self.status = transfer.status
                return transfer
        except Exception:
            if self.status != Transfer.TransactionStatus.FAILED:
                self.status = Transfer.TransactionStatus.FAILED
                self.save(update_fields=['status'])
            raise

    def __str__(self):
        return f"Transfer of {self.sender} to {self.receiver}"
