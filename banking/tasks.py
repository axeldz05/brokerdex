from celery import shared_task

from .services import WithdrawalService


@shared_task
def recover_pending_withdrawals():
    return WithdrawalService.recover_pending()
