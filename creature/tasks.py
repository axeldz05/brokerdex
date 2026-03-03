# related tasks for auto battle between creatures
from celery import shared_task
from creature.models import Creature

@shared_task
def battle_matchmaking():
    # Como las criaturas tienen un campo que dice si estan disponibles para
    # pelear, basta con buscar en la lista de criaturas quienes estan disponibles
    # y ponerles en un match, teniendo en cuenta el poder relativo entre ambos y, luego, 
    # otras cuestiones como rachas y tipos.
    creature1 = Creature.objects.filter(is_available_for_battle=True) 
    creature2 = Creature.objects.filter(is_available_for_battle=True) 
