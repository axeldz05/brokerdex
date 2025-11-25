from django.views.generic import ListView
from .models import Battle, Creature

def show_battles(request):
    if request.method == 'GET':
        battles = Battle.objects.all()
        print(battles.creatures)

