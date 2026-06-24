from django.urls import path
from . import views

app_name = 'creature'
urlpatterns = [
    path('incubation/', views.incubation_shop_view, name='incubation_shop'),
    path('incubation/purchase/<uuid:egg_id>/', views.purchase_egg_view, name='purchase_egg'),
    path('incubation/status/', views.incubation_status_view, name='incubation_status'),
    path('training/<uuid:portfolio_id>/', views.train_creature_view, name='train_creature'),
    path('battles/', views.battle_list_view, name='battle_list'),
    path('battles/<int:battle_id>/', views.battle_detail_view, name='battle_detail'),
    path('battles/creature/<uuid:creature_id>/', views.creature_battle_history_view, name='creature_battle_history'),
]
