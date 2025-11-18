from django.urls import path
from . import views

app_name = 'banking'
urlpatterns = [
    path('exchange/', views.exchange, name='exchange'),
    path('withdraw/', views.withdraw, name='withdraw'),
    path('deposit/', views.deposit, name='deposit'),
    path('invest/', views.invest, name='invest')
]
