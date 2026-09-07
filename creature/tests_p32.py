from importlib.util import find_spec
from io import StringIO
from unittest.mock import Mock, patch

from django.core.management import call_command
from django.test import SimpleTestCase


class LegacyImportRemovalTests(SimpleTestCase):
    def test_unused_modules_are_removed(self):
        for module_name in (
            "creature.celery",
            "creature.serializers",
            "creature.storage",
            "django_filters",
            "rest_framework",
        ):
            with self.subTest(module_name=module_name):
                self.assertIsNone(find_spec(module_name))


class GenerateRandomBattleCommandTests(SimpleTestCase):
    @patch("creature.services.BattleService.matchmake_random_battle")
    def test_command_delegates_to_battle_service(self, matchmake):
        matchmake.return_value = Mock(id=42)
        output = StringIO()

        call_command(
            "generate_random_battle",
            stdout=output,
            no_color=True,
        )

        matchmake.assert_called_once_with()
        self.assertEqual(output.getvalue(), "Battle created: 42.\n")

    @patch("creature.services.BattleService.matchmake_random_battle")
    def test_command_reports_when_no_battle_is_created(self, matchmake):
        matchmake.return_value = None
        output = StringIO()

        call_command(
            "generate_random_battle",
            stdout=output,
            no_color=True,
        )

        matchmake.assert_called_once_with()
        self.assertEqual(
            output.getvalue(),
            "No battle created: fewer than two creatures are available.\n",
        )
