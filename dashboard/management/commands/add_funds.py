import argparse
from uuid import uuid4

from django.core.management.base import BaseCommand, CommandError

from account.models import Account
from banking.services import DepositService


def positive_int(value):
    ivalue = int(value)
    if ivalue <= 0:
        raise argparse.ArgumentTypeError(f"{value} is not a positive integer")
    return ivalue


class Command(BaseCommand):
    help = "Adds funds to a user's balance"

    def add_arguments(self, parser):
        parser.add_argument("username", type=str)
        parser.add_argument("amount", type=positive_int)

    def handle(self, *args, **options):
        username = options["username"]
        amount = options["amount"]

        if amount <= 0:
            self.stdout.write(self.style.ERROR('El monto debe ser positivo'))
            return

        try:
            account = Account.objects.get(username=username)
            DepositService.credit_completed(
                provider_reference=f"manual:add-funds:{uuid4()}",
                target_identifier=account.cvu,
                amount_cents=amount,
                identifier_type='CVU',
            )
            self.stdout.write(
                self.style.SUCCESS(
                    f'An amount of {amount} cents was added to '
                    f"{username}'s balance"
                )
            )

        except Account.DoesNotExist:
            raise CommandError(f'The user "{username}" does not exist')
        except Exception as e:
            raise CommandError(f'Error: {str(e)}')


