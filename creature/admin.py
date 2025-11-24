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

    def clean_abilities(self):
        return [ability.pk for ability in self.cleaned_data['abilities']]

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        if self.instance and self.instance.pk:
            self.fields['abilities'].initial = Ability.objects.filter(pk__in=self.instance.abilities)

@admin.register(Creature)
class CreatureAdmin(admin.ModelAdmin):
    form = CreatureForm

def import_csv(csv_file, batch_size=1000):
    with open(csv_file, 'r') as f:
        reader = csv.DictReader(f)
        batch = []
        for row in reader:
            batch.append(Creature(
                **{field: row[field] for field in row if field != 'id'}
            ))
            if len(batch) > batch_size:
                with transaction.atomic():
                    Creature.objects.bulk_create(batch)
                batch = []
        if batch:
            with transaction.atomic():
                Creature.objects.bulk_create(batch)
