import secrets

from django.db import migrations
from django.db.models import Q


CVU_LENGTH = 22
ALIAS_TOKEN_BYTES = 8


def _generate_cvu(used_cvus):
    while True:
        cvu = f"{secrets.randbelow(10 ** CVU_LENGTH):0{CVU_LENGTH}d}"
        if cvu not in used_cvus:
            return cvu


def _generate_alias(used_aliases):
    while True:
        token = secrets.token_hex(ALIAS_TOKEN_BYTES)
        alias = f"brokerdex.{token[:8]}.{token[8:]}"
        if alias not in used_aliases:
            return alias


def backfill_account_identifiers(apps, schema_editor):
    Account = apps.get_model("account", "Account")
    database = schema_editor.connection.alias
    accounts = Account.objects.using(database)
    used_cvus = set(
        accounts.exclude(cvu__isnull=True)
        .exclude(cvu="")
        .values_list("cvu", flat=True)
    )
    used_aliases = set(
        accounts.exclude(alias__isnull=True)
        .exclude(alias="")
        .values_list("alias", flat=True)
    )
    missing_identifiers = accounts.filter(
        Q(cvu__isnull=True)
        | Q(cvu="")
        | Q(alias__isnull=True)
        | Q(alias="")
    )

    for account in missing_identifiers.iterator():
        update_fields = []
        if not account.cvu:
            account.cvu = _generate_cvu(used_cvus)
            used_cvus.add(account.cvu)
            update_fields.append("cvu")
        if not account.alias:
            account.alias = _generate_alias(used_aliases)
            used_aliases.add(account.alias)
            update_fields.append("alias")
        account.save(
            using=database,
            update_fields=update_fields,
        )


class Migration(migrations.Migration):
    dependencies = [
        ("account", "0001_initial"),
    ]

    operations = [
        migrations.RunPython(
            backfill_account_identifiers,
            migrations.RunPython.noop,
        ),
    ]
