from django.core.management import call_command
from django.core.management.base import BaseCommand


class Command(BaseCommand):
    help = "Deprecated alias for seed_all."

    def add_arguments(self, parser):
        parser.add_argument(
            "--clear",
            action="store_true",
            help="Pass --clear through to seed_all.",
        )

    def handle(self, *args, **options):
        self.stdout.write(self.style.WARNING(
            "seed_market is deprecated; use seed_all instead."
        ))
        call_command(
            "seed_all",
            clear=options["clear"],
            stdout=self.stdout,
        )
