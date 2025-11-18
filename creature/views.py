from django.views.generic import ListView
from .models import Creature

class CreatureListView(ListView):
    model = Creature
    template_name = 'stocks/stock_list.html'
    context_object_name = 'stocks'
    paginate_by = 50
    
    def get_queryset(self):
        queryset = Creature.objects.all().only(
            'symbol', 'name', 'current_price', 'previous_close', 'small_icon', 'large_icon'
        )
        ordering = self.request.GET.get('ordering', 'symbol')
        return queryset.order_by(ordering)
