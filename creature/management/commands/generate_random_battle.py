from django.core.management.base import BaseCommand
from creature.models import Creature, Battle, BattleOutcome, BattleParticipant

class Command(BaseCommand):
    help = 'Generate random battle every 5 minutes'

    def handle(self, *args, **options):
        creaturesQuerySet = Creature.objects.filter(currently_in_battle=False)
        creature1 = creaturesQuerySet[0]
        creature2 = creaturesQuerySet[1]
        battle = Battle.objects.create()
        BattleParticipant.objects.create(battle=battle, creature=creature1, survived=True)
        BattleParticipant.objects.create(battle=battle, creature=creature2, survived=False)

        print(battle.creatures)
# Primero, usar celery para manejar las batallas. Los ataques seran aleatorios.
# Haria falta hacer al menos una batalla, ver como funciona, luego implementar celery para
# crear las batallas segun la disponibilidad de las creaturas con un "looking_for_battle"
# cuando las criaturas terminan una batalla, ambas entran en un battle_cooldown de nuevo, recuperan vida,
# y actualizan su cotizacion.
