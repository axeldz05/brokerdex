from django.urls import path
from . import views

app_name = 'banking'
urlpatterns = [
    path('', views.index, name="index"),
    path('customers', views.customers, name="customers"),
    path('customers/search', views.customers_search, name="customers_search"),
]
