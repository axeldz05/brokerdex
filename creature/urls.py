from django.urls import path
from . import views

app_name = 'creature'
urlpatterns = [
    path('incubation/', views.incubation_shop_view, name='incubation_shop'),
    path('incubation/purchase/<uuid:egg_id>/', views.purchase_egg_view, name='purchase_egg'),
    path('incubation/status/', views.incubation_status_view, name='incubation_status'),
]
