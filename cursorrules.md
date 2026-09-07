# Brokerdex: reglas de ejecución para Cursor

Este archivo define cómo investigar, implementar, verificar y operar el
proyecto. La arquitectura y las invariantes completas están en `AGENTS.md`.

## Secuencia obligatoria de trabajo

1. Antes de leer archivos completos, buscar símbolos y patrones con `rg`/glob.
2. Revisar `git status` y no sobrescribir cambios ajenos.
3. Elegir la capa responsable: form/view para HTTP, servicio para negocio,
   modelo/manager para persistencia y task para delegación asíncrona.
4. Para código, aplicar `.agents/skills/django-tdd/SKILL.md`: test rojo, cambio
   mínimo y test verde. Para documentación, contrastar cada afirmación con el
   código/configuración real y validar comandos y enlaces.
5. Al terminar cualquier implementación:
   1. volver a validar con `django-tdd`;
   2. aplicar `.agents/skills/semantic-compression/SKILL.md`;
   3. aplicar `.agents/skills/continuous-granularity/SKILL.md`;
   4. repetir tests estrechos, tests de apps afectadas, `uv run flake8`,
      `manage.py check` y control de migraciones.
6. No crear commits salvo pedido explícito del usuario.

No se considera terminada una implementación hasta completar el paso 5.

## Gestión del entorno

El entorno canónico usa Python 3.13, Django 5.2.x, PostgreSQL, Redis y Celery.

Todo comando Python se ejecuta mediante `uv run`; no invocar directamente
`python`, `pytest`, `flake8`, `celery` ni otros entrypoints. Las dependencias
se sincronizan desde el lock:

```bash
uv sync --locked
uv run python manage.py migrate
```

- Usar `uv add`/`uv remove`, nunca `pip`.
- Después de cambiar modelos, crear una migración nueva e inspeccionarla.
- No editar migraciones compartidas ni regenerar datos de producción.

### Settings y variables

- `brokerdex.settings.local` es el default de `manage.py` y Celery; puede leer
  `.env`.
- `brokerdex.settings.test` mantiene PostgreSQL y usa `TEST_DATABASE_URL` con
  fallback a `DATABASE_URL`.
- `brokerdex.settings.production` no lee `.env`: el runtime debe inyectar
  variables.
- Producción exige `DJANGO_SECRET_KEY`, `DJANGO_ALLOWED_HOSTS`,
  `DATABASE_URL` y `REDIS_URL`.
- `DEPOSIT_WEBHOOK_SECRET` habilita depósitos firmados. No registrar ni mostrar
  su valor.
- Las variables y defaults completos están en `docs/operations.md`.

### Procesos

`Procfile` es producción:

- `web`: Gunicorn sobre `brokerdex.wsgi`;
- `worker`: worker Celery con settings de producción;
- `beat`: única instancia del scheduler con settings de producción.

`Procfile.dev` usa `runserver`, worker y Beat locales. `run.sh` comprueba
`uv`, Django, PostgreSQL y Redis, rechaza un Beat duplicado y delega a Honcho:

```bash
bash run.sh
```

`run.sh` no inicia PostgreSQL ni Redis. Mantener una sola instancia de Beat.
Para la suite normal se parchea Celery; para el round-trip real usar
`docs/celery-smoke.md`.

PostgreSQL es obligatorio. SQLite cambia constraints, transacciones,
`distinct`, tipos y `select_for_update`.

### Datos

```bash
uv run python manage.py seed_all
uv run python manage.py seed_all --demo
```

`seed_all --clear --demo` es destructivo y requiere confirmación explícita en
una base local descartable. El seed puede descargar imágenes desde PokeAPI; no
usarlo como setup de test. `seed_market` solo es un alias deprecado.

## Verificación proporcional al cambio

```bash
# Test individual durante TDD
uv run python manage.py test app.tests.Clase.test_caso -v 2

# Apps afectadas
uv run python manage.py test trading creature banking account dashboard

# Gates comunes
uv run flake8
uv run python manage.py check
uv run python manage.py makemigrations --check --dry-run
```

- Tests normales: broker y servicios externos mockeados.
- Tests de concurrencia: PostgreSQL real y `TransactionTestCase`.
- Cambios financieros: comprobar centavos, `Decimal`, comisión, reservas,
  rollback, locks y hechos persistidos.
- Cambios Celery: comprobar servicio síncrono, adaptador task, retry,
  recuperación e idempotencia.
- Cambios HTTP/templates: comprobar auth, status/redirect, mensajes, contexto,
  accesibilidad y contrato JSON.
- Ejecutar suite completa al tocar modelos compartidos, motores financieros,
  batallas, settings, middleware o migraciones.
- GitHub Actions ejecuta `uv sync --locked`, flake8, check, migraciones, suite
  y `check --deploy --fail-level WARNING`. Reproducir localmente cualquier
  fallo antes de modificar el workflow.

## Operación segura

- Nunca ejecutar dos schedulers Celery Beat sobre la misma cola.
- No ejecutar seeds destructivos, flush, borrado de DB o rollback de
  migraciones sin autorización.
- No esconder fallos de Redis: distinguir scheduling, ejecución y
  compensación.
- No mantener una transacción abierta mientras se espera red externa.
- No usar datos demo como requisito implícito de código o tests.
- No leer, imprimir ni copiar archivos de credenciales retirados. Su
  eliminación no reemplaza la rotación descrita en
  `docs/security/credential-rotation.md`.
- No imprimir URLs de base/Redis completas, secretos HMAC, firmas ni payloads
  bancarios.
- En producción, ejecutar `migrate`, `collectstatic` y checks antes de cambiar
  tráfico; WhiteNoise no sirve media.
- `/health/ready/` solo valida PostgreSQL y Redis. Para probar consumo real del
  worker ejecutar el smoke Celery; no arrancar Beat para ese smoke.

## Límites de refactor

- Conservar nombres de rutas, contratos JSON y contexto de templates salvo
  cambio explícito. Las rutas bancarias canónicas están en la raíz.
- Conservar unidades: centavos, precios a 2 decimales y cantidades a 8.
- Conservar atomicidad, orden de locks, reservas e historial de
  `Trade`/`Transfer`.
- No mover lógica de negocio a views, templates, JavaScript o tasks.
- No exponer ratings ni snapshots ELO; el porcentaje de impacto sí es contrato
  de presentación.
- No convertir el ORM en repositorios genéricos ni ocultar `QuerySet`, locks o
  transacciones.
- No “arreglar de paso” deuda fuera de alcance; documentarla o protegerla con
  un test si bloquea.
