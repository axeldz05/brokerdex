import csv
from django import forms
from django.contrib import admin
from django.db import transaction
from .models import Creature, Ability

admin.site.register(Ability)

class AbilityModelMultipleChoiceField(forms.ModelMultipleChoiceField):
    # This field will show the list of Ability objects
    pass

class CreatureForm(forms.ModelForm):
    abilities = AbilityModelMultipleChoiceField(
        queryset=Ability.objects.all(),
        widget=forms.SelectMultiple,
        required=False
    )

    class Meta:
        model = Creature 
        fields = '__all__'

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        if self.instance and self.instance.pk:
            self.fields['abilities'].initial = self.instance.abilities.all()

@admin.register(Creature)
class CreatureAdmin(admin.ModelAdmin):
    form = CreatureForm

def import_csv(csv_file, batch_size=1000):
    with open(csv_file, 'r') as f:
        reader = csv.DictReader(f)
        batch = []
        for row in reader:
            data = {field: row[field] for field in row if field != 'id'}
            if 'pricing_reference_price' not in data and 'current_price' in data:
                data['pricing_reference_price'] = data['current_price']
            batch.append(Creature(**data))
            if len(batch) > batch_size:
                with transaction.atomic():
                    Creature.objects.bulk_create(batch)
                batch = []
        if batch:
            with transaction.atomic():
                Creature.objects.bulk_create(batch)
