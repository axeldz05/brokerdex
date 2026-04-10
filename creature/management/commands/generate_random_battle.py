from django.core.management.base import BaseCommand
from creature.models import Creature, Battle, BattleOutcome, BattleParticipant

class Command(BaseCommand):
    def handle(self, *args, **options):
        creaturesQuerySet = Creature.objects.filter(currently_in_battle=False)
        creature1 = creaturesQuerySet[0]
        creature2 = creaturesQuerySet[1]
        battle = Battle.objects.start_battle(self.charmander, self.squirtle)
        print(battle.creatures)
