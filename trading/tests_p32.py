from importlib import import_module
from io import StringIO
from unittest.mock import ANY, patch

from django.core.management import call_command
from django.test import SimpleTestCase


class SeedMarketCommandTests(SimpleTestCase):
    def test_deprecated_command_delegates_to_seed_all(self):
        command_module = import_module(
            "trading.management.commands.seed_market"
        )
        output = StringIO()

        with patch.object(
            command_module,
            "call_command",
            create=True,
        ) as seed_all:
            call_command(
                "seed_market",
                "--clear",
                stdout=output,
                no_color=True,
            )

        seed_all.assert_called_once_with(
            "seed_all",
            clear=True,
            stdout=ANY,
        )
        self.assertEqual(
            output.getvalue(),
            "seed_market is deprecated; use seed_all instead.\n",
        )
