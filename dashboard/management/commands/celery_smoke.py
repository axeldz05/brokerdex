"""Run an explicit Celery broker/result-backend round-trip."""

from urllib.parse import urlsplit
from uuid import uuid4

from django.core.management.base import BaseCommand, CommandError

from brokerdex.celery import app


def _scheme(configured_url):
    if not configured_url:
        return 'unconfigured'
    return urlsplit(str(configured_url)).scheme or 'configured'


class Command(BaseCommand):
    help = (
        'Publish a safe Celery task and read its result. '
        'Requires Redis and a worker; never starts Celery Beat.'
    )

    def add_arguments(self, parser):
        parser.add_argument(
            '--timeout',
            type=float,
            default=10,
            help='Seconds to wait for the worker result (default: 10).',
        )
        parser.add_argument(
            '--queue',
            help='Optional queue consumed by the target worker.',
        )

    def handle(self, *args, **options):
        timeout = options['timeout']
        if timeout <= 0:
            raise CommandError('--timeout must be greater than zero.')

        broker_url = app.conf.broker_url
        result_backend = app.conf.result_backend
        if not broker_url:
            raise CommandError('Celery broker is not configured.')
        if not result_backend or result_backend == 'disabled://':
            raise CommandError('Celery result backend is not configured.')

        publish_options = {}
        if options['queue']:
            publish_options['queue'] = options['queue']

        token = str(uuid4())
        try:
            result = app.send_task(
                'celery.accumulate',
                args=[token],
                kwargs={'index': 0},
                **publish_options,
            )
            received = result.get(timeout=timeout)
        except Exception as error:
            error_name = type(error).__name__
            raise CommandError(
                'Celery round-trip failed '
                f'({error_name}); verify Redis and worker configuration.'
            ) from None

        if received != token:
            raise CommandError(
                'Celery worker returned an unexpected smoke payload.'
            )

        try:
            result.forget()
        except Exception as error:
            error_name = type(error).__name__
            raise CommandError(
                'Celery round-trip passed but result cleanup failed '
                f'({error_name}).'
            ) from None

        self.stdout.write(self.style.SUCCESS(
            'Celery smoke passed '
            f'(broker={_scheme(broker_url)}, '
            f'result_backend={_scheme(result_backend)}).'
        ))
