from django.core.management import call_command
from django.core.management.base import BaseCommand


class Command(BaseCommand):
    help = 'Alias for "python manage.py bots --stream" to launch the bot simulation stream.'

    def add_arguments(self, parser):
        parser.add_argument(
            '--interval',
            type=float,
            default=2.5,
            help='Seconds between ticks (default: 2.5).',
        )
        parser.add_argument(
            '--max-ticks',
            type=int,
            default=None,
            help='Maximum number of ticks before stopping.',
        )

    def handle(self, *args, **options):
        call_command(
            'bots',
            '--stream',
            interval=options.get('interval', 2.5),
            max_ticks=options.get('max_ticks'),
        )
