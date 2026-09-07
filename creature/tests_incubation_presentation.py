from datetime import timedelta
from unittest.mock import patch

from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from account.models import Account

from .models import EggTemplate, Incubation


class IncubationPresentationTestCase(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.user = Account.objects.create_user(
            username='incubation-viewer',
            email='incubation-viewer@example.com',
            password='testpass123',
        )
        cls.egg = EggTemplate.objects.create(
            name='Presentation Egg',
            description='An egg used in presentation tests.',
            price='500.00',
            hatch_duration=timedelta(hours=1),
        )

    def setUp(self):
        self.client.force_login(self.user)

    def create_incubation(
        self,
        *,
        now,
        status=Incubation.Status.INCUBATING,
        elapsed=timedelta(minutes=30),
        remaining=timedelta(minutes=30),
    ):
        incubation = Incubation.objects.create(
            user=self.user,
            egg_template=self.egg,
            hatches_at=now + remaining,
            status=status,
        )
        Incubation.objects.filter(pk=incubation.pk).update(
            purchased_at=now - elapsed,
        )
        incubation.refresh_from_db()
        return incubation

    def test_progress_percent_uses_incubation_timestamps(self):
        now = timezone.now()
        incubation = self.create_incubation(now=now)

        with patch('creature.models.timezone.now', return_value=now):
            self.assertEqual(incubation.progress_percent, 50)

    def test_progress_percent_is_clamped_between_zero_and_one_hundred(self):
        now = timezone.now()
        incubation = self.create_incubation(now=now)

        with patch(
            'creature.models.timezone.now',
            return_value=incubation.purchased_at - timedelta(minutes=1),
        ):
            self.assertEqual(incubation.progress_percent, 0)

        with patch(
            'creature.models.timezone.now',
            return_value=incubation.hatches_at + timedelta(minutes=1),
        ):
            self.assertEqual(incubation.progress_percent, 100)

    def test_status_page_presents_incubating_state_and_real_progress(self):
        now = timezone.now()
        self.create_incubation(now=now)

        with patch('creature.models.timezone.now', return_value=now):
            response = self.client.get(reverse('creature:incubation_status'))

        incubation = response.context['incubations'][0]
        self.assertEqual(incubation.progress_percent, 50)
        self.assertContains(response, 'Incubating')
        self.assertContains(response, 'role="progressbar"')
        self.assertContains(response, 'aria-valuenow="50"')
        self.assertContains(response, 'aria-valuemin="0"')
        self.assertContains(response, 'aria-valuemax="100"')
        self.assertContains(response, 'style="width: 50%;"')

    def test_status_page_presents_hatched_state(self):
        now = timezone.now()
        Incubation.objects.create(
            user=self.user,
            egg_template=self.egg,
            hatches_at=now - timedelta(hours=1),
            status=Incubation.Status.HATCHED,
            hatched_at=now,
        )

        response = self.client.get(reverse('creature:incubation_status'))

        self.assertContains(response, 'Hatched!')
        self.assertNotContains(response, 'Failed')

    def test_status_page_presents_failed_state_refund_and_safe_reason(self):
        now = timezone.now()
        internal_reason = (
            'OperationalError at redis://worker:secret@internal:6379/0'
        )
        Incubation.objects.create(
            user=self.user,
            egg_template=self.egg,
            hatches_at=now - timedelta(hours=1),
            status=Incubation.Status.FAILED,
            failed_at=now,
            failure_reason=internal_reason,
            refunded_at=now,
        )

        response = self.client.get(reverse('creature:incubation_status'))

        self.assertContains(response, 'Failed')
        self.assertContains(response, 'Purchase refunded')
        self.assertContains(
            response,
            'This egg could not be hatched because of a processing problem.',
        )
        self.assertNotContains(response, 'Hatched!')
        self.assertNotContains(response, internal_reason)

    def test_shop_uses_real_progress_with_accessible_metadata(self):
        now = timezone.now()
        self.create_incubation(
            now=now,
            elapsed=timedelta(minutes=15),
            remaining=timedelta(minutes=45),
        )

        with patch('creature.models.timezone.now', return_value=now):
            response = self.client.get(reverse('creature:incubation_shop'))

        self.assertContains(response, 'role="progressbar"')
        self.assertContains(response, 'aria-valuenow="25"')
        self.assertContains(response, 'style="width: 25%;"')
