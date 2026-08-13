from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ('trading', '0003_notification'),
    ]

    operations = [
        migrations.AddField(
            model_name='trade',
            name='realized_cost_basis',
            field=models.DecimalField(
                blank=True,
                decimal_places=2,
                help_text=(
                    'Cost basis consumed by a SELL execution; NULL for '
                    'historical trades without a snapshot'
                ),
                max_digits=12,
                null=True,
            ),
        ),
    ]
