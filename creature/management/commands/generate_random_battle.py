from django.core.management.base import BaseCommand

from creature.services import BattleService


class Command(BaseCommand):
    help = "Start a random battle between two available creatures."

    def handle(self, *args, **options):
        battle = BattleService.matchmake_random_battle()
        if battle is None:
            self.stdout.write(self.style.WARNING(
                "No battle created: fewer than two creatures are available."
            ))
            return

        self.stdout.write(self.style.SUCCESS(
            f"Battle created: {battle.id}."
        ))
