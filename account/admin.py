from django.contrib import admin
from django.contrib.auth.admin import UserAdmin
from .models import Account


@admin.register(Account)
class AccountAdmin(UserAdmin):
    list_display = (
        'username',
        'email',
        'balance_cents',
        'cvu',
        'alias',
        'is_staff',
        'is_active',
    )
    search_fields = ('username', 'email', 'cvu', 'alias')
    readonly_fields = ('cvu', 'alias', 'date_joined', 'last_login')
    fieldsets = (
        (None, {'fields': ('username', 'password')}),
        ('Personal info', {'fields': (
            'email',
            'phone',
            'country',
            'city',
            'address',
            'postal_or_zip_code',
        )}),
        ('Financial info', {'fields': ('balance_cents', 'cvu', 'alias')}),
        ('Permissions', {'fields': (
            'is_active',
            'is_staff',
            'is_superuser',
            'groups',
            'user_permissions',
        )}),
        ('Important dates', {'fields': ('last_login', 'date_joined')}),
    )
