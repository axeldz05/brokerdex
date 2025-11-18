import csv
from django.contrib import admin
from django.db import transaction
from .models import Creature

# Register your models here.
admin.site.register(Creature)



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
