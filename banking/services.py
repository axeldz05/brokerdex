from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from django.db.models import Q
from django.utils import timezone

from account.models import Account


class DepositService:
    """Credit completed deposits exactly once per provider reference."""

    IDENTIFIER_FIELDS = {
        "CVU": "cvu",
        "ALIAS": "alias",
    }

    @classmethod
    def credit_completed(
        cls,
        provider_reference,
        target_identifier,
        amount_cents,
        identifier_type="CVU",
    ):
        from .models import Transfer

        cls._validate_input(
            provider_reference=provider_reference,
            target_identifier=target_identifier,
            identifier_type=identifier_type,
            amount_cents=amount_cents,
        )

        try:
            with transaction.atomic():
                existing = cls._find_existing(
                    provider_reference,
                    for_update=True,
                )
                if existing:
                    return existing

                account_field = cls.IDENTIFIER_FIELDS[identifier_type]
                try:
                    receiver = Account.objects.select_for_update().get(
                        **{account_field: target_identifier}
                    )
                except Account.DoesNotExist as error:
                    raise ValidationError(
                        "Receiver account not found",
                        code="account_not_found",
                    ) from error

                existing = cls._find_existing(
                    provider_reference,
                    for_update=True,
                )
                if existing:
                    return existing

                transfer = Transfer.objects.create(
                    sender=None,
                    receiver=receiver,
                    type=Transfer.TransactionType.DEPOSIT,
                    status=Transfer.TransactionStatus.COMPLETED,
                    amount=amount_cents,
                    provider_reference=provider_reference,
                    target_identifier=target_identifier,
                    description=f"Deposit via {identifier_type}",
                    processed_at=timezone.now(),
                )
                receiver.balance_cents += amount_cents
                receiver.save(update_fields=["balance_cents"])
                return transfer
        except IntegrityError:
            existing = cls._find_existing(
                provider_reference,
            )
            if existing:
                return existing
            raise

    @staticmethod
    def _find_existing(
        provider_reference,
        for_update=False,
    ):
        from .models import Transfer

        queryset = Transfer.objects.filter(
            type=Transfer.TransactionType.DEPOSIT,
            provider_reference=provider_reference,
        )
        if for_update:
            queryset = queryset.select_for_update()
        return queryset.first()

    @classmethod
    def _validate_input(
        cls,
        provider_reference,
        target_identifier,
        identifier_type,
        amount_cents,
    ):
        if (
            not isinstance(provider_reference, str)
            or not provider_reference.strip()
            or len(provider_reference) > 255
        ):
            raise ValidationError(
                "Provider reference is required.",
                code="invalid_payload",
            )
        if identifier_type not in cls.IDENTIFIER_FIELDS:
            raise ValidationError(
                "Type of identifier not supported.",
                code="invalid_payload",
            )
        if (
            not isinstance(target_identifier, str)
            or not target_identifier.strip()
            or len(target_identifier) > 100
        ):
            raise ValidationError(
                "Target identifier is required.",
                code="invalid_payload",
            )
        if (
            not isinstance(amount_cents, int)
            or isinstance(amount_cents, bool)
            or amount_cents <= 0
        ):
            raise ValidationError(
                "Amount must be higher than zero",
                code="invalid_payload",
            )


class ExternalBankService:
    def send_money(
        self,
        source_cvu,
        target_cvu,
        amount,
        idempotency_key,
    ):
        """Submit an idempotent provider request keyed by the transfer ID."""
        return True


class WithdrawalService:
    """Reserve, submit, and reconcile external withdrawals safely."""

    @classmethod
    def initiate(cls, sender, target_identifier, amount_cents):
        from .models import Transfer

        if amount_cents <= 0:
            raise ValidationError("Amount must be higher than zero")
        if not sender.cvu:
            raise ValidationError("Source account requires a CVU.")

        with transaction.atomic():
            sender = Account.objects.select_for_update().get(pk=sender.pk)
            if sender.balance_cents < amount_cents:
                raise ValidationError("Insufficient Funds")

            transfer = Transfer.objects.create(
                sender=sender,
                receiver=None,
                type=Transfer.TransactionType.WITHDRAW,
                amount=amount_cents,
                status=Transfer.TransactionStatus.PENDING,
                source_identifier=sender.cvu,
                target_identifier=target_identifier,
                description=f"Withdraw to CVU {target_identifier}",
            )
            sender.balance_cents -= amount_cents
            sender.save(update_fields=['balance_cents'])

        transfer = cls.process_pending(transfer.pk)
        if transfer.status == Transfer.TransactionStatus.FAILED:
            raise ValidationError("The bank rejected the transaction.")
        return transfer

    @classmethod
    def process_pending(cls, transfer_id, bank_service=None):
        from .models import Transfer

        now = timezone.now()
        stale_before = now - timezone.timedelta(
            seconds=getattr(
                settings,
                'WITHDRAWAL_PROCESSING_TIMEOUT_SECONDS',
                300,
            )
        )
        with transaction.atomic():
            transfer = (
                Transfer.objects.select_for_update()
                .get(pk=transfer_id)
            )
            if transfer.status in [
                Transfer.TransactionStatus.COMPLETED,
                Transfer.TransactionStatus.FAILED,
            ]:
                return transfer
            if (
                transfer.status == Transfer.TransactionStatus.PROCESSING
                and transfer.processing_started_at
                and transfer.processing_started_at > stale_before
            ):
                return transfer
            if transfer.type != Transfer.TransactionType.WITHDRAW:
                raise ValidationError("Only withdrawals can be submitted.")
            source_cvu = transfer.source_identifier
            if not source_cvu and transfer.sender_id:
                source_cvu = Account.objects.filter(
                    pk=transfer.sender_id
                ).values_list('cvu', flat=True).first()
            if not source_cvu:
                raise ValidationError("Withdrawal source account is missing.")

            transfer.status = Transfer.TransactionStatus.PROCESSING
            transfer.provider_attempts += 1
            transfer.provider_error = ''
            transfer.processing_started_at = now
            transfer.save(update_fields=[
                'status',
                'provider_attempts',
                'provider_error',
                'processing_started_at',
            ])
            target_identifier = transfer.target_identifier
            amount_cents = transfer.amount
            idempotency_key = str(transfer.exchange_id)

        bank_service = bank_service or ExternalBankService()
        try:
            success = bank_service.send_money(
                source_cvu=source_cvu,
                target_cvu=target_identifier,
                amount=amount_cents,
                idempotency_key=idempotency_key,
            )
        except Exception as error:
            return cls._record_uncertain_failure(transfer_id, error)

        if success:
            return cls._mark_completed(transfer_id)
        return cls._mark_failed_and_refund(
            transfer_id,
            "The bank rejected the transaction.",
        )

    @classmethod
    def recover_pending(cls, bank_service=None):
        from .models import Transfer

        stale_before = timezone.now() - timezone.timedelta(
            seconds=getattr(
                settings,
                'WITHDRAWAL_PROCESSING_TIMEOUT_SECONDS',
                300,
            )
        )
        transfer_ids = list(
            Transfer.objects.filter(
                type=Transfer.TransactionType.WITHDRAW,
            ).filter(
                Q(status=Transfer.TransactionStatus.PENDING)
                | Q(
                    status=Transfer.TransactionStatus.PROCESSING,
                    processing_started_at__lte=stale_before,
                )
            ).values_list('pk', flat=True)
        )
        for transfer_id in transfer_ids:
            cls.process_pending(
                transfer_id,
                bank_service=bank_service,
            )
        return len(transfer_ids)

    @classmethod
    def _record_uncertain_failure(cls, transfer_id, error):
        from .models import Transfer

        with transaction.atomic():
            transfer = Transfer.objects.select_for_update().get(pk=transfer_id)
            if transfer.status == Transfer.TransactionStatus.PROCESSING:
                transfer.status = Transfer.TransactionStatus.PENDING
                transfer.provider_error = str(error)
                transfer.save(update_fields=['status', 'provider_error'])
            return transfer

    @classmethod
    def _mark_completed(cls, transfer_id):
        from .models import Transfer

        with transaction.atomic():
            transfer = Transfer.objects.select_for_update().get(pk=transfer_id)
            if transfer.status == Transfer.TransactionStatus.PROCESSING:
                transfer.status = Transfer.TransactionStatus.COMPLETED
                transfer.processed_at = timezone.now()
                transfer.processing_started_at = None
                transfer.save(update_fields=[
                    'status',
                    'processed_at',
                    'processing_started_at',
                ])
            return transfer

    @classmethod
    def _mark_failed_and_refund(cls, transfer_id, error):
        from .models import Transfer

        with transaction.atomic():
            transfer = Transfer.objects.select_for_update().get(pk=transfer_id)
            if transfer.status in [
                Transfer.TransactionStatus.COMPLETED,
                Transfer.TransactionStatus.FAILED,
            ]:
                return transfer

            sender = Account.objects.select_for_update().get(
                pk=transfer.sender_id
            )
            sender.balance_cents += transfer.amount
            sender.save(update_fields=['balance_cents'])

            transfer.status = Transfer.TransactionStatus.FAILED
            transfer.provider_error = str(error)
            transfer.processed_at = timezone.now()
            transfer.processing_started_at = None
            transfer.save(update_fields=[
                'status',
                'provider_error',
                'processed_at',
                'processing_started_at',
            ])
            return transfer
