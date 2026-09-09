from celery import shared_task

from brokerdex.locks import prevent_task_overlap
from .services import WithdrawalService


@shared_task
@prevent_task_overlap(timeout=120)
def recover_pending_withdrawals():
    return WithdrawalService.recover_pending()
