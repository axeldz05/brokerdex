---
description: Flujo obligatorio de arquitectura, TDD, refactor y operación de Brokerdex
alwaysApply: true
---

# Brokerdex

- Buscar con `rg`/glob antes de leer o implementar; reutilizar patrones reales.
- Leer `AGENTS.md` para arquitectura e invariantes y `cursorrules.md` para operación.
- No sobrescribir trabajo ajeno ni crear commits sin pedido explícito.
- PostgreSQL es el motor canónico; no reemplazarlo con SQLite.
- Usar `uv` y `uv.lock`; Redis respalda Celery broker/result backend.
- Views/forms manejan HTTP; services contienen negocio; models/managers expresan persistencia; tasks delegan.
- Dinero: saldo entero en centavos, precios `Decimal(12,2)`, cantidades `Decimal(18,8)`.
- Mutaciones financieras usan `transaction.atomic()` y objetos reconsultados con `select_for_update()`.
- Tasks y servicios reintentables deben ser idempotentes.
- No exponer ELO ni reescribir migraciones compartidas.

Para cada implementación:

1. Aplicar `.agents/skills/django-tdd/SKILL.md`.
2. Implementar el mínimo cambio verde.
3. Aplicar `.agents/skills/semantic-compression/SKILL.md`.
4. Aplicar `.agents/skills/continuous-granularity/SKILL.md`.
5. Repetir tests, `manage.py check` y `makemigrations --check --dry-run`.

Una implementación no está terminada hasta completar esta secuencia.
