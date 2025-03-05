from django.urls import path
from . import views

app_name = 'account'
urlpatterns = [
    path('account/login/', views.log_in, name="login"),
    path('account/register/', views.register, name="register"),
    path('account/logout/', views.log_out, name="logout"),
    path('account/settings/', views.settings, name="settings"),
]
