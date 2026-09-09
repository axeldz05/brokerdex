# Operación de Brokerdex

Este runbook describe la configuración que existe en el repositorio. La
arquitectura y las invariantes de dominio están en [AGENTS.md](../AGENTS.md).

## Runtime soportado

- Python 3.13 y `uv`; instalar siempre desde `uv.lock`.
- Django 5.2.x con PostgreSQL.
- Celery 5.6.x con Redis como broker y result backend.
- Daphne para ASGI (HTTP + WebSockets) y WhiteNoise para static en producción.

Verificación del runtime:

```bash
uv sync --locked
uv lock --check
uv run python --version
uv run python -c "import django; print(django.get_version())"
```

## Settings por ambiente

`brokerdex.settings.local`

- Es el default de `manage.py`, `brokerdex.celery` y
  `brokerdex.settings`.
- Lee `.env` cuando `DJANGO_READ_DOT_ENV_FILE=True`, que es el default.
- Tiene defaults solo de desarrollo para secret, hosts, PostgreSQL y Redis.
- Usa logging legible y no activa WhiteNoise.

`brokerdex.settings.test`

- Usa `DEBUG=False` y una secret fija exclusiva para tests.
- Exige PostgreSQL: `TEST_DATABASE_URL` prevalece y cae a `DATABASE_URL`.
- Usa `REDIS_URL` o Redis DB 15 por default. La suite normal sustituye Celery y
  no necesita que Redis esté activo.

`brokerdex.settings.production`

- No lee archivos `.env`; el entorno del proceso debe inyectar configuración.
- Fuerza `DEBUG=False`.
- Activa cookies seguras, redirect HTTPS, HSTS, logs JSON y WhiteNoise con
  manifest comprimido.
- WSGI y ASGI lo seleccionan por default, salvo que el proceso ya defina otro
  `DJANGO_SETTINGS_MODULE`.

## Variables de entorno

Obligatorias en producción:

- `DJANGO_SETTINGS_MODULE=brokerdex.settings.production`.
- `DJANGO_SECRET_KEY`: secret larga generada fuera del repositorio.
- `DJANGO_ALLOWED_HOSTS`: hosts separados por comas, sin esquema.
- `DATABASE_URL`: URL PostgreSQL.
- `REDIS_URL`: URL Redis usada tanto por broker como por result backend.

Necesarias según el despliegue:

- `DJANGO_CSRF_TRUSTED_ORIGINS`: orígenes completos separados por comas, por
  ejemplo `https://app.example.com`.
- `DEPOSIT_WEBHOOK_SECRET`: secreto compartido del webhook. Si está vacío, el
  endpoint rechaza todas las solicitudes con `invalid_signature`.
- `PORT`: consumida por el comando Daphne de `Procfile`; default 8000.
- `DJANGO_TRUST_X_FORWARDED_PROTO=True`: solo detrás de un proxy confiable que
  elimina el header entrante y establece `X-Forwarded-Proto`.

Controles de seguridad con defaults productivos:

- `DJANGO_CSRF_COOKIE_SECURE=True`.
- `DJANGO_SESSION_COOKIE_SECURE=True`.
- `DJANGO_SECURE_SSL_REDIRECT=True`.
- `DJANGO_SECURE_HSTS_SECONDS=31536000`.
- `DJANGO_SECURE_HSTS_INCLUDE_SUBDOMAINS=True`.
- `DJANGO_SECURE_HSTS_PRELOAD=True`.

Variables locales/de test:

- `DJANGO_READ_DOT_ENV_FILE`: solo lo consulta `local.py`.
- `DJANGO_DEBUG`: solo lo consulta `local.py`.
- `TEST_DATABASE_URL`: solo lo consulta `test.py`.

No existen hoy variables `LOG_LEVEL`, `BATTLE_TURN_INTERVAL` ni
`WITHDRAWAL_PROCESSING_TIMEOUT_SECONDS`. El nivel de log está fijado en `INFO`;
el turno de batalla está definido en 180 segundos en settings base; el timeout
de recuperación de retiros usa un default de 300 segundos en el servicio.

`.env.example` es un punto de partida local, no un manifiesto productivo.
Actualmente no incluye `DEPOSIT_WEBHOOK_SECRET`: agregarla de forma privada al
entorno que habilite depósitos y nunca guardar su valor en el repositorio.

## Arranque local

Preparar las variables locales y dependencias:

```bash
cp .env.example .env
uv sync --locked
pg_isready -h localhost -p 5432
redis-cli -u redis://localhost:6379/0 ping
uv run python manage.py migrate
```

Arrancar web, worker y un único Beat:

```bash
bash run.sh
```

El script no crea PostgreSQL ni Redis. Primero ejecuta el check Django, abre una
conexión real a ambos servicios, verifica que no haya otro Beat de Brokerdex y
delega a:

```bash
uv run honcho start -f Procfile.dev
```

Para trabajo sin asincronía puede iniciarse solo el proceso requerido. Para
incubación, training, batallas, recuperación o ejecución periódica de órdenes
se necesita Redis y worker; Beat solo es necesario al probar schedules.

## Release de producción

Antes de cambiar tráfico, con todas las variables productivas inyectadas:

```bash
uv sync --locked
DJANGO_SETTINGS_MODULE=brokerdex.settings.production uv run python manage.py check --deploy --fail-level WARNING
DJANGO_SETTINGS_MODULE=brokerdex.settings.production uv run python manage.py makemigrations --check --dry-run
DJANGO_SETTINGS_MODULE=brokerdex.settings.production uv run python manage.py migrate
DJANGO_SETTINGS_MODULE=brokerdex.settings.production uv run python manage.py collectstatic --noinput
```

`Procfile` declara:

```text
web: uv run daphne -b 0.0.0.0 -p ${PORT:-8000} brokerdex.asgi:application
worker: DJANGO_SETTINGS_MODULE=brokerdex.settings.production uv run celery -A brokerdex worker --loglevel=info
beat: DJANGO_SETTINGS_MODULE=brokerdex.settings.production uv run celery -A brokerdex beat --loglevel=info
```

Condiciones de salida del release:

- migraciones aplicadas una sola vez antes de escalar procesos;
- static recolectado para el mismo artefacto de código;
- al menos un web y un worker saludables;
- exactamente un Beat por entorno/cola;
- liveness y readiness verdes;
- smoke Celery verde cuando el cambio toca infraestructura asíncrona;
- webhook firmado probado cuando el cambio toca depósitos.

No revertir migraciones automáticamente durante un rollback. Primero confirmar
compatibilidad del esquema con la versión anterior; si no existe, preparar una
migración correctiva revisada.

## Static y media

`STATIC_ROOT` es `staticfiles/`. Producción coloca
`WhiteNoiseMiddleware` después de `SecurityMiddleware` y usa
`CompressedManifestStaticFilesStorage`. WhiteNoise sirve static recolectado,
incluidos assets del admin; no sirve uploads.

`MEDIA_ROOT` es `media/` y el storage default sigue siendo filesystem. Django
solo añade la ruta `/media/` cuando `DEBUG=True`. Un despliegue productivo debe:

1. montar almacenamiento durable y servir `/media/` fuera de Django, o
2. implementar/configurar un storage de objetos antes de aceptar uploads.

Un filesystem efímero sin servidor de media pierde o vuelve inaccesibles los
sprites. Los CDN usados por templates tampoco son reemplazados por
`collectstatic`.

## Health, logs y request ID

`GET /health/live/`

- siempre responde `200 {"status": "ok"}`;
- no consulta servicios externos;
- sirve como liveness del proceso web.

`GET /health/ready/`

- ejecuta `SELECT 1` en PostgreSQL;
- hace `PING` al Redis configurado, con timeout de conexión y lectura de 0,5 s;
- responde 200 cuando ambos están disponibles y 503 en otro caso;
- no confirma que worker o Beat estén consumiendo.

`RequestIdMiddleware` es el primer middleware. Acepta `X-Request-ID` solo si es
un UUID válido; en otro caso genera uno. El mismo valor se expone como
`request.request_id`, se incorpora al contexto de logging y vuelve en el header
de respuesta.

Local/test usan logs legibles. Producción emite un objeto JSON por línea con
`timestamp`, `level`, `logger`, `message`, `request_id` y `exception` cuando
corresponde. No registrar secretos, firmas, payloads bancarios completos ni
URLs con credenciales. El request ID no se propaga hoy a Celery.

## Schedules Celery

Beat contiene:

- matchmaking cada 5 minutos;
- recuperación de incubaciones, trainings y batallas cada minuto;
- recuperación de retiros cada minuto;
- precios cada minuto;
- órdenes límite cada 30 segundos;
- OHLCV 1H al inicio de cada hora;
- OHLCV 1D a las 00:00 UTC;
- OHLCV 1W cada lunes a las 00:00 UTC;
- índices de mercado cada día a las 00:30 UTC.

Los turnos de batalla siguientes se agendan desde la task con countdown; no son
una entrada adicional de Beat.

El round-trip real broker → worker → result backend se valida con el
[smoke de Celery](celery-smoke.md). No arrancar Beat para ese smoke.

## CI y gates locales

GitHub Actions usa Python 3.13, PostgreSQL 17, Redis 7 y
`brokerdex.settings.test`:

```bash
uv sync --locked
uv run flake8
DJANGO_SETTINGS_MODULE=brokerdex.settings.test uv run python manage.py check
DJANGO_SETTINGS_MODULE=brokerdex.settings.test uv run python manage.py makemigrations --check --dry-run
DJANGO_SETTINGS_MODULE=brokerdex.settings.test uv run python manage.py test
```

El job termina con `check --deploy --fail-level WARNING` sobre
`brokerdex.settings.production`. Redis vivo en CI garantiza disponibilidad de
infraestructura para tests que lo necesiten, pero el smoke con worker real
sigue siendo un gate manual.

Gates adicionales según riesgo:

- dinero/concurrencia: tests `TransactionTestCase` sobre PostgreSQL;
- migraciones de datos: revisar ida, compatibilidad y volumen;
- Celery: servicio síncrono, task, retry, recovery, idempotencia y smoke real;
- static: ejecutar `collectstatic --noinput` en el artefacto;
- media: probar persistencia/serving en la infraestructura elegida;
- depósitos: seguir el [contrato HMAC](deposit-webhook.md);
- credenciales retiradas: completar
  [rotación y eventual purga](security/credential-rotation.md).
