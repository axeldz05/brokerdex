from django.contrib.auth.models import BaseUserManager
from django.db import IntegrityError, transaction
from django.db.models import Q
from django.utils.translation import gettext_lazy as _

from .identifiers import generate_account_identifiers


IDENTIFIER_GENERATION_ATTEMPTS = 5


class AccountManager(BaseUserManager):
    use_in_migrations = True

    def create_user(self, username, email, password=None, **extra_fields):
        if not email:
            raise ValueError(_('The Email must be set'))
        if not username:
            raise ValueError(_('The Username must be set'))
        email = self.normalize_email(email)
        user = self.model(username=username, email=email, **extra_fields)
        user.set_password(password)
        generate_cvu = not user.cvu
        generate_alias = not user.alias

        for attempt in range(IDENTIFIER_GENERATION_ATTEMPTS):
            cvu, alias = generate_account_identifiers()
            if generate_cvu:
                user.cvu = cvu
            if generate_alias:
                user.alias = alias

            try:
                with transaction.atomic(using=self._db):
                    user.save(using=self._db, force_insert=True)
            except IntegrityError:
                has_collision = self._generated_identifier_collision(
                    user,
                    check_cvu=generate_cvu,
                    check_alias=generate_alias,
                )
                if (
                    not has_collision
                    or attempt == IDENTIFIER_GENERATION_ATTEMPTS - 1
                ):
                    raise
            else:
                return user

    def _generated_identifier_collision(
        self,
        user,
        *,
        check_cvu,
        check_alias,
    ):
        identifiers = Q()
        if check_cvu:
            identifiers |= Q(cvu=user.cvu)
        if check_alias:
            identifiers |= Q(alias=user.alias)
        return bool(identifiers) and self.filter(identifiers).exists()

    def create_superuser(self, username, email, password, **extra_fields):
        extra_fields.setdefault('is_staff', True)
        extra_fields.setdefault('is_superuser', True)
        extra_fields.setdefault('is_active', True)

        if extra_fields.get('is_staff') is not True:
            raise ValueError(_('Superuser must have is_staff=True.'))
        if extra_fields.get('is_superuser') is not True:
            raise ValueError(_('Superuser must have is_superuser=True.'))
        return self.create_user(username, email, password, **extra_fields)
