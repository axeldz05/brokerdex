"""
URL configuration for bank project.

The `urlpatterns` list routes URLs to views. For more information please see:
    https://docs.djangoproject.com/en/5.1/topics/http/urls/
Examples:
Function views
    1. Add an import:  from my_app import views
    2. Add a URL to urlpatterns:  path('', views.home, name='home')
Class-based views
    1. Add an import:  from other_app.views import Home
    2. Add a URL to urlpatterns:  path('', Home.as_view(), name='home')
Including another URLconf
    1. Import the include() function: from django.urls import include, path
    2. Add a URL to urlpatterns:  path('blog/', include('blog.urls'))
"""
from django.conf.urls.static import static
from django.conf import settings
from django.contrib import admin
from django.urls import include, path
from django.views.generic import RedirectView
from . import views


def _legacy_banking_redirect(route_name):
    return RedirectView.as_view(
        pattern_name=f'banking:{route_name}',
        permanent=True,
        query_string=True,
    )


urlpatterns = [
    path('health/live/', views.health_live, name='health_live'),
    path('health/ready/', views.health_ready, name='health_ready'),
    path('sw.js', views.sw_js, name='sw_js'),
    path(
        'banking/transfer/',
        _legacy_banking_redirect('transfer'),
        name='legacy_banking_transfer',
    ),
    path(
        'banking/withdraw/',
        _legacy_banking_redirect('withdraw'),
        name='legacy_banking_withdraw',
    ),
    path(
        'banking/deposit/',
        _legacy_banking_redirect('deposit'),
        name='legacy_banking_deposit',
    ),
    path(
        'banking/invest/',
        _legacy_banking_redirect('invest'),
        name='legacy_banking_invest',
    ),
    path('', include('banking.urls')),
    path('', include('account.urls')),
    path('', include('dashboard.urls')),
    path('', include('creature.urls')),
    path('', include('trading.urls')),
    path('', views.index, name='index'),
    path('admin/', admin.site.urls),
]

if settings.DEBUG:
    urlpatterns += static(
        settings.MEDIA_URL,
        document_root=settings.MEDIA_ROOT,
    )
