# Brokerdex: contexto y reglas para agentes

Este documento describe el estado real del repositorio. `.agents/skills/`
contiene los procedimientos obligatorios de TDD y refactor, y `.agents/rules/`
define las reglas operativas de ejecución.

## 1. Producto, stack y runtime

Brokerdex es una plataforma de trading gamificada: las criaturas son activos
fraccionables, las batallas alteran una referencia de valuación y el Market
Maker cubre únicamente la liquidez que no aporta el libro P2P.

- Python 3.13 administrado con `uv`; `pyproject.toml` y `uv.lock` son
  autoritativos.
- Django 5.2.x con PostgreSQL. No se soporta SQLite.
- Celery 5.6.x con Redis como broker y result backend.
- Templates Django, Bootstrap 5, Chart.js y dependencias visuales por CDN.
- Auth propia con `account.Account(AbstractBaseUser, PermissionsMixin)`.
- Gunicorn sirve WSGI en producción y WhiteNoise sirve solo archivos static.
- GitHub Actions ejecuta lint, checks Django, control de migraciones, suite y
  checks de despliegue.

La configuración vive en `brokerdex/settings/`:

- `base.py`: apps, middleware, logging, storage base, Celery Beat e invariantes
  compartidas.
- `local.py`: default de `manage.py` y Celery, lee `.env` por defecto, permite
  valores locales de desarrollo.
- `test.py`: `DEBUG=False`, PostgreSQL desde `TEST_DATABASE_URL` o
  `DATABASE_URL`, Redis DB 15 por defecto.
- `production.py`: exige secret, hosts, PostgreSQL y Redis desde el entorno;
  activa seguridad, logs JSON y WhiteNoise.
- `__init__.py`: alias compatible a settings locales.

`brokerdex/wsgi.py` y `brokerdex/asgi.py` usan producción como default;
`manage.py` y `brokerdex/celery.py` usan local. Un
`DJANGO_SETTINGS_MODULE` ya definido siempre prevalece.

## 2. Mapa del repositorio

```text
account/       identidad, autenticación, CVU/alias y saldo
banking/       transferencias, depósitos y retiros
creature/      criaturas, batallas, incubación y entrenamiento
dashboard/     resumen financiero y ledger
trading/       órdenes, fills, portfolio, precios, índices y alertas
brokerdex/     settings, URLs, observabilidad, WSGI/ASGI y Celery
templates/     layout y páginas públicas
seed_data/     catálogo y datos demo
docs/          runbooks operativos y seguridad
.agents/       reglas y skills del proyecto (Gemini Antigravity)
```

### `account`

- `Account` conserva `USERNAME_FIELD = "username"`, email único, CVU, alias,
  perfil, flags Django y `balance_cents`.
- `AUTHENTICATION_BACKENDS` registra
  `EmailOrUsernameModelBackend`; el email se compara sin distinguir mayúsculas
  y el username conserva su comparación exacta.
- `log_in` usa `AccountAuthenticationForm`. Registro y manager generan CVU de
  22 dígitos y alias únicos con reintento ante colisiones.
- La migración `0002_backfill_account_identifiers` completa identificadores
  faltantes existentes. Settings los presenta como datos de solo lectura.
- `balance_dollars` y `balance_audit` son compatibilidad; la fuente financiera
  autoritativa es `balance_cents`.
- Rutas: `/account/login/`, `/account/register/`, `/account/logout/` y
  `/account/settings/`.

### `banking`

- `Transfer` persiste centavos y estados `pending`, `processing`, `completed` y
  `failed`; los tipos son transferencia, depósito y retiro.
- La transferencia interna crea el hecho, vuelve a validar saldo bajo locks de
  cuentas ordenados y completa todo dentro de una transacción.
- `WithdrawalService` reserva fondos, usa `exchange_id` como idempotency key
  externo, no mantiene locks durante la llamada de red y recupera operaciones
  pendientes cada minuto. `ExternalBankService` sigue siendo un stub.
- `WithdrawForm` compara su `Decimal` en dólares contra `balance_cents`
  convertido explícitamente. Este flujo y las views bancarias tienen tests
  HTTP y de concurrencia.
- `DepositService` acredita bajo lock por CVU o alias. Una restricción
  condicional hace única la `provider_reference` de depósitos y protege
  reintentos/concurrencia.
- `POST /webhooks/deposits/` valida HMAC-SHA256 del body exacto con
  `DEPOSIT_WEBHOOK_SECRET`; el contrato completo está en
  `docs/deposit-webhook.md`.
- Rutas canónicas: `/transfer/`, `/withdraw/`, `/deposit/`, `/invest/`.
  `/banking/<ruta>/` existe solo como redirect permanente compatible.

### `creature`

Modelos principales:

- `Creature`: UUID, tipos, precio actual, `pricing_reference_price`, stats,
  habilidades, rareza, imágenes, circuit breaker, cooldown, ELO interno,
  victorias y derrotas.
- `Battle`: estado, turno, winner, snapshots ELO internos, impactos
  porcentuales persistidos, `next_turn_at` y `result_processed_at`.
- `BattleParticipant`, `BattleAction` y `BattleInvestment`: estado por combate,
  acción única por turno e inversión observada.
- `EggTemplate`/`Incubation` y `TrainingSession`: compra/costo, ejecución
  diferida, estado de scheduling, resultado, notificación y compensación.

Servicios y asincronía:

- `IncubationService` cobra bajo lock, persiste antes de agendar, recupera
  scheduling y acredita el portfolio una sola vez. Un error permanente
  reembolsa una sola vez.
- `TrainingService` cobra 10 % del precio actual en centavos, crea una sesión y
  agenda un boost de 1–5 puntos. Errores transitorios reintentan; al agotarse o
  ante error permanente, el costo se devuelve atómicamente una sola vez y la
  sesión queda `FAILED`.
- `BattleService` controla matchmaking, turnos, ELO, pricing, inversiones,
  historial y notificaciones. `result_processed_at` evita reprocesar el
  resultado.
- `recover_creature_operations` recupera incubaciones, entrenamientos y
  batallas vencidas cada minuto.
- `matchmake_random_battle` corre cada cinco minutos y los turnos usan
  `BATTLE_TURN_INTERVAL=180` definido en settings.

El rating ELO y sus snapshots no forman parte de templates ni payloads
públicos. La UI sí muestra el porcentaje potencial de cambio: no confundir el
impacto publicado con el rating interno.

### `trading`

Modelos:

- `Portfolio`: único por owner+creature, cantidad `Decimal(18,8)`, costo medio
  `Decimal(12,2)` y P&L derivado.
- `Order`: BUY/SELL, MARKET/LIMIT, cantidad llena, reservas exactas y estados
  OPEN/FILLED/PARTIALLY_FILLED/CANCELLED.
- `Trade`: un fill histórico con precio, cantidad, comisión y costo realizado;
  contraparte nula representa Market Maker.
- `PriceHistory`: velas OHLCV 1H/1D/1W únicas por criatura, intervalo y bucket.
- `MarketIndex` y `Notification`: snapshots de índice y alertas de usuario.

Servicios:

- `TradingEngine` implementa prioridad precio-tiempo, cruces P2P, fills
  parciales, reservas, cancelación y Market Maker para el remanente. Locks de
  criatura, órdenes, cuentas y portfolios se adquieren en orden estable.
- `PricingEngine` calcula
  `pricing_reference_price × (1 + presión_mercado_24h) × rareza`. La batalla
  modifica de forma persistente la referencia; el pricing periódico no vuelve
  a sumar win rate histórico ni compone el shock.
- `record_price_snapshot` agrega trades del último bucket UTC completo,
  transporta el cierre previo cuando no hay trades y usa `update_or_create`.
  Beat genera 1H, 1D y 1W; la semanal corre lunes 00:00 UTC.
- `price_history_api` entrega como máximo 168 velas 1H, 365 1D y 52 1W,
  ordenadas ascendentemente. La vista usa candlesticks, volumen y fallback
  tabular accesible.
- `MarketIndicesService` pondera el precio actual de cada criatura por todo su
  volumen de trades persistido. Sin volumen usa promedio simple. El índice se
  calcula diariamente.
- `VolatilityService` activa un halt de 15 minutos y notifica holders cuando un
  recálculo supera 10 %.
- `trading.accounting` es el contrato compartido de portfolio, dashboard y API;
  no volver a acoplar dashboard a helpers privados de views.

### `dashboard` y UI

- `dashboard.ledger.build_dashboard_ledger` combina hechos completados de
  `Transfer` y todos los `Trade` del usuario en entradas `Decimal` firmadas y
  ordenadas de forma descendente.
- Compras incluyen subtotal+comisión; ventas subtotal-comisión; depósitos,
  retiros y transferencias conservan sus centavos históricos.
- Dashboard y portfolio comparten `trading.accounting` para income/expenses y
  net flow mensual/anual.
- Hay 24 templates server-rendered. Los assets visuales principales se cargan
  por CDN; no existe un pipeline frontend local.

## 3. Flujos críticos

### Compra o venta market

La view valida shape y llama a `TradingEngine`. El motor bloquea criatura,
órdenes elegibles, cuentas y portfolios; consume el libro P2P por prioridad y
manda solo el remanente al Market Maker. Cada fill crea un `Trade`; la orden
market puede abarcar varios fills.

### Orden limit

- BUY reserva precio×cantidad más margen de comisión persistido en
  `reserved_cents`.
- SELL reserva unidades en `reserved_quantity`.
- Cruces P2P se ejecutan al precio de la orden resting y pueden dejar estado
  `PARTIALLY_FILLED`.
- Cancelar libera solo el remanente exacto.
- Cambios requieren tests de precio-tiempo, rounding por fill, doble ejecución,
  cancelación y concurrencia PostgreSQL.

### Transferencia y retiro

Transferencias internas revalidan saldo bajo lock. El retiro descuenta antes de
llamar al proveedor; rechazo definitivo reembolsa y timeout vuelve a
`pending` para recuperación con la misma idempotency key.

### Depósito webhook

La view autentica el body crudo; `DepositService` valida payload, bloquea la
cuenta y persiste el crédito. La referencia del proveedor es la clave de
idempotencia y debe ser globalmente única para esta integración.

### Incubación y entrenamiento

El servicio cobra y persiste la operación antes de `transaction.on_commit`.
Fallas de scheduling quedan recuperables; las tasks delegan el efecto al
servicio. Efectos terminales y reembolsos se deduplican por estado persistido.

### Batalla y shock de precio

Al iniciar se guardan snapshots internos e impactos potenciales. Al terminar,
el resultado se bloquea, actualiza ELO/wins/losses, aplica un shock simétrico a
`pricing_reference_price`, recalcula precios, guarda snapshots post-batalla y
notifica. Un draw no altera la referencia.

## 4. Invariantes no negociables

### Precisión financiera

- Saldo y transferencias: enteros en centavos.
- Precios y dinero: `DecimalField(12,2)` y `Decimal`.
- Fracciones y volumen: `DecimalField(18,8)`.
- Construir `Decimal` desde strings y cuantizar con la política existente.
- `float` solo en bordes de presentación/JSON compatibles; nunca en
  persistencia o cálculo financiero.

### Atomicidad y concurrencia

- Toda mutación de saldo, orden, portfolio, incubación o entrenamiento usa
  `transaction.atomic()`.
- Reconsultar dentro del bloque con `select_for_update()` y adquirir locks en
  orden estable.
- No realizar red externa con locks abiertos cuando el flujo puede persistir un
  estado recuperable primero.
- Concurrencia real se prueba con PostgreSQL y `TransactionTestCase`.

### Asincronía

- Tasks finas: adaptar argumentos, clasificar retry y delegar.
- Services invocables sin worker.
- Retry exige clave/estado persistente de idempotencia.
- No duplicar fills, hatch, boost, refund, resultado ni notificación.
- Solo una instancia de Celery Beat por entorno.

### Capas y contratos

- Forms validan entrada; views controlan auth/HTTP/mensajes; services negocio;
  models/managers persistencia; tasks/commands orquestación.
- Preservar nombres de URL, keys JSON y contexto de templates salvo cambio
  explícito.
- `Trade`, `Transfer` y snapshots son hechos históricos; no recalcularlos con
  reglas actuales.
- ELO nunca se expone. No registrar secretos, firmas, URLs con credenciales ni
  cuerpos bancarios completos.

### Datos y esquema

- No editar ni borrar migraciones compartidas; agregar migraciones nuevas.
- No cambiar `AUTH_USER_MODEL`, unidades, UUIDs o relaciones históricas como
  limpieza lateral.
- No ejecutar `seed_all --clear` ni modificar seed data sin objetivo y
  autorización explícitos.

## 5. Operación actual

- `Procfile` es producción: Gunicorn, worker y un Beat con settings de
  producción.
- `Procfile.dev` es desarrollo: `runserver`, worker y un Beat con settings
  locales.
- `run.sh` consolida el entorno de desarrollo: asegura PostgreSQL (servicio/contenedor y creación de `brokerdex_db`), Redis, limpieza de procesos huérfanos de Celery, chequeos, migraciones y seed inicial si la base está vacía, delegando finalmente a Honcho con `Procfile.dev`.
- WhiteNoise sirve `STATIC_ROOT`; producción requiere ejecutar
  `collectstatic`. No sirve media.
- `/health/live/` no consulta dependencias. `/health/ready/` consulta
  PostgreSQL y Redis con timeout corto y devuelve 503 si falla alguno.
- `RequestIdMiddleware` conserva un `X-Request-ID` UUID válido o crea uno, lo
  devuelve y lo añade a logs. Local usa formato legible; producción JSON.
- El smoke real de Celery es manual y está en `docs/celery-smoke.md`.

Consultar `docs/operations.md` antes de desplegar.

## 6. Deuda real restante

No reintroducir como deuda problemas ya resueltos: `WithdrawForm` usa centavos
correctamente, los índices ponderan por volumen, el motor produce fills
parciales y `makemigrations --check --dry-run` no debe detectar cambios.

Deuda o límites todavía presentes:

- `ExternalBankService.send_money` es un stub; no existe adaptador de proveedor,
  consulta de estado ni conciliación real.
- El webhook usa un secreto HMAC compartido sin timestamp/ventana anti-replay ni
  namespace de proveedor. Una referencia repetida deduplica, pero no audita si
  el segundo payload contradice monto o destino.
- `DEPOSIT_WEBHOOK_SECRET` está soportado por local/producción pero no figura en
  `.env.example`; sin valor, el endpoint rechaza todas las firmas.
- `Account.cvu` y `alias` siguen siendo nullable para escrituras ORM directas;
  manager y migración completan el flujo normal, pero el esquema no garantiza
  presencia.
- El default storage de media es filesystem y Django no expone `/media/` con
  `DEBUG=False`. Producción necesita volumen durable/servidor externo o un
  backend de objetos.
- Logs usan nivel `INFO` fijo; no hay métricas, tracing ni propagación de
  request-id a tasks Celery.
- Readiness prueba broker Redis, no confirma que un worker o Beat estén
  consumiendo. El smoke Celery no corre en CI.
- Fallos repetidos de scheduling de incubación/training quedan pendientes para
  recovery sin dead-letter ni umbral terminal automático.
- `balance_dollars` mantiene un setter basado en `float` por compatibilidad.
- `seed_market` es un alias deprecado a `seed_all`; también se conserva el
  adaptador de mensajes training antiguos y redirects bancarios legacy.
- La UI depende de CDNs y no hay bundle local ni política CSP declarada.

No corregir deuda fuera de alcance. Si bloquea un cambio, caracterizar primero
el comportamiento y corregir el mínimo.

## 7. Áreas de alto riesgo

- `TradingEngine`, `DepositService`, `WithdrawalService`,
  `IncubationService` y `TrainingService`: dinero, reservas y locks.
- `BattleService.process_battle_result` y `process_battle_turn`: idempotencia,
  ELO oculto y pricing persistente.
- `PricingEngine`, `MarketIndicesService` y `VolatilityService`.
- Estados/vínculos de `Order`, `Trade`, `Transfer`, `Portfolio`, `Battle`,
  `Incubation` y `TrainingSession`.
- Migraciones, datos seed, contratos JSON/templates y nombres `web`, `worker`,
  `beat`.
- `brokerdex/celery.py` es la única app Celery canónica.

Si una tarea toca estas áreas, escribir primero el test de
caracterización/regresión.

## 8. Refactor obligatorio

Después de hacer verde una implementación:

1. aplicar `.agents/skills/semantic-compression/SKILL.md`;
2. aplicar `.agents/skills/continuous-granularity/SKILL.md`;
3. conservar primitivas ORM/transaccionales disponibles;
4. evitar repositorios genéricos, signals financieros, DTOs duplicados,
   jerarquías prematuras y god services;
5. repetir tests.

Las abstracciones nacen de dos o más casos reales. Ocultar locks, unidades,
reservas o efectos asíncronos es una regresión.

## 9. Gates y comandos

La suite normal parchea Celery y bordes externos; no requiere Redis ni worker.
PostgreSQL sí es obligatorio.

```bash
uv sync --locked
uv run flake8
DJANGO_SETTINGS_MODULE=brokerdex.settings.test uv run python manage.py check
DJANGO_SETTINGS_MODULE=brokerdex.settings.test uv run python manage.py makemigrations --check --dry-run
DJANGO_SETTINGS_MODULE=brokerdex.settings.test uv run python manage.py test
```

El gate de despliegue requiere variables de producción y:

```bash
DJANGO_SETTINGS_MODULE=brokerdex.settings.production uv run python manage.py check --deploy --fail-level WARNING
```

Seeding y operación:

```bash
uv run python manage.py migrate
uv run python manage.py seed_all
uv run python manage.py seed_all --demo
uv run python manage.py add_funds <username> <amount_en_centavos>
bash run.sh
```

Consultar `cursorrules.md`, `.agents/skills/django-tdd/SKILL.md` y
`docs/operations.md` para el ciclo completo.
