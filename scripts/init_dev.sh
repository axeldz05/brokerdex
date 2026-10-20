#!/usr/bin/env bash
set -euo pipefail

echo "Running database migrations..."
uv run python manage.py migrate --no-input

echo "Checking if initial catalog seeding is required..."
uv run python - <<'PY'
import os
import django

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "brokerdex.settings.local")
django.setup()

from creature.models import Creature

if not Creature.objects.exists():
    from django.core.management import call_command
    print("Database has no creatures. Seeding initial Pokémon catalog...")
    call_command("seed_all")
else:
    print("Database already contains creatures. Skipping catalog seed.")
PY

echo "Database initialization complete."
