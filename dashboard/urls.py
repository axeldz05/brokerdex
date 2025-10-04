from django.urls import include, path
from . import views

app_name = 'dashboard'
urlpatterns = [
    path('dashboard/', views.dashboard, name="dashboard"),
    path('banking/', include('banking.urls')),
]
