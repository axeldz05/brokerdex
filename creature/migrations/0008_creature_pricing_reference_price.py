from django.db import migrations, models


def copy_current_price_to_reference(apps, schema_editor):
    Creature = apps.get_model('creature', 'Creature')
    Creature.objects.update(
        pricing_reference_price=models.F('current_price')
    )


class Migration(migrations.Migration):
    dependencies = [
        (
            'creature',
            '0007_trainingsession_alter_battleparticipant_options_and_more',
        ),
    ]

    operations = [
        migrations.AddField(
            model_name='creature',
            name='pricing_reference_price',
            field=models.DecimalField(
                decimal_places=2,
                editable=False,
                help_text=(
                    'Stable reference used by the dynamic pricing engine'
                ),
                max_digits=12,
                null=True,
            ),
        ),
        migrations.RunPython(
            copy_current_price_to_reference,
            migrations.RunPython.noop,
        ),
        migrations.AlterField(
            model_name='creature',
            name='pricing_reference_price',
            field=models.DecimalField(
                decimal_places=2,
                editable=False,
                help_text=(
                    'Stable reference used by the dynamic pricing engine'
                ),
                max_digits=12,
            ),
        ),
    ]
