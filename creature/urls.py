from django.urls import path
from . import views

app_name = 'creature'
urlpatterns = [
    path('creatures/battles/', views.show_battles, name="battles"),
]
