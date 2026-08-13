# Brokerdex: contexto y reglas para agentes

Este documento describe el estado real del repositorio. `cursorrules.md` define el flujo operativo y `.cursor/skills/` contiene los procedimientos obligatorios de TDD y refactor.

## 1. Producto y stack actual

Brokerdex es una plataforma de trading gamificada: las criaturas son activos financieros fraccionables, las batallas afectan su valuación y el Market Maker actúa cuando no hay contraparte P2P.

- Python 3.13 administrado con `uv` (`pyproject.toml` + `uv.lock`).
- Django 5.2.x con PostgreSQL.
- Celery 5.6.x con Redis en `redis://localhost:6379/0`.
- Django Templates, Bootstrap 5, Chart.js e iconos cargados por CDN.
- Auth propia con `account.Account(AbstractBaseUser, PermissionsMixin)`.
- DRF está instalado, pero la UI y la mayoría de endpoints actuales usan views Django y `JsonResponse`.
- `Procfile` levanta `web`, `worker` y `beat`; `honcho` los orquesta en desarrollo.
- No hay CI, settings por ambiente, `.env.example`, servidor web productivo ni pipeline de static files.

La configuración actual de `brokerdex/settings.py` es local y no production-ready: contiene `SECRET_KEY`, `DEBUG=True`, PostgreSQL y Redis hardcodeados. No asumir que ya existe configuración por ambiente.

## 2. Mapa del repositorio

```text
account/       identidad, perfil y saldo
banking/       transferencias, depósitos y retiros
creature/      criaturas, habilidades, batallas, incubación y entrenamiento
dashboard/     resumen principal del usuario
trading/       portfolio, órdenes, trades, precios, índices y alertas
brokerdex/     settings, URLs raíz, WSGI/ASGI y app Celery canónica
templates/     layout y páginas públicas
seed_data/     JSON de criaturas, habilidades, huevos y demo
.cursor/       reglas y skills del proyecto
```

### `account`

- `Account`: username, email, CVU, alias, perfil, flags Django y `balance_cents`.
- `USERNAME_FIELD = "username"`; `AccountManager` crea usuarios/superusuarios.
- `EmailOrUsernameModelBackend` existe, pero `settings.py` no declara `AUTHENTICATION_BACKENDS`; no afirmar que el login por email está activo sin verificar/configurar.
- `AccountAuthenticationForm` existe, pero la view de login usa actualmente `AuthenticationForm`.
- `balance_dollars` y `balance_audit` son compatibilidad existente; la fuente financiera autoritativa es `balance_cents`.
- Rutas: `/account/login/`, `/account/register/`, `/account/logout/`, `/account/settings/`.

### `banking`

- `Transfer` guarda monto entero en centavos y estados pending/completed/failed.
- Tipos: transferencia interna, depósito externo y retiro externo.
- `TransferManager` contiene `create_transaction`, `process_external_deposit` y `process_external_withdrawal`.
- `ExternalBankService` es un stub/borde externo y debe mockearse en tests.
- `create_transaction` valida el saldo antes del lock y no lo revalida después; tratarlo como riesgo de concurrencia, no como garantía de doble gasto resuelta.
- Rutas: `/transfer/`, `/withdraw/`, `/deposit/`, `/invest/`.

### `creature`

Modelos:

- `Creature`: UUID, tipos, precio, stats, hasta cuatro habilidades, evolución, rareza, imágenes, circuit breaker, cooldown, estado de batalla, ELO interno, victorias y derrotas.
- `Ability`: tipo, power/accuracy/PP/priority, damage class, target y atributos de estado/healing.
- `Battle`: estado, turno, winner, snapshots ELO, cambios potenciales y `next_turn_at`.
- `BattleParticipant`: HP/estado y snapshots de precio pre/post batalla.
- `BattleAction`: acción persistida por turno.
- `BattleInvestment`: cantidad de inversores/holdings observados por batalla y turno.
- `EggTemplate` e `Incubation`: catálogo, compra, ETA, resultado y task id.

Servicios:

- `IncubationService`: compra atómica y hatch que acredita un `Portfolio`.
- `BattleService`: matchmaking/lifecycle, ELO, cambios de precio, historial, leaderboard, notificaciones e inversiones por turno.
- `TrainingService`: costo del 10 %, débito y scheduling del boost.

Celery:

- `matchmake_random_battle` corre cada 5 minutos.
- `process_battle_turn` encadena turnos según `BATTLE_TURN_INTERVAL` (180 s por defecto).
- `hatch_egg` y `complete_training` procesan trabajos diferidos.
- Las views cubren tienda/estado de incubación, entrenamiento, lista/detalle/historial de batallas.

Rutas: `/incubation/`, `/incubation/status/`, `/training/<uuid>/`, `/battles/`, `/battles/<id>/` y `/battles/creature/<uuid>/`.

### `trading`

Modelos:

- `Portfolio`: único por owner+creature; quantity `Decimal(18,8)`, average cost `Decimal(12,2)` y P&L derivado.
- `Order`: BUY/SELL, MARKET/LIMIT, filled quantity y estados OPEN/FILLED/PARTIALLY_FILLED/CANCELLED.
- `Trade`: registro de ejecución con comisión histórica; `seller=NULL` en compras y `buyer=NULL` en ventas representan al Market Maker.
- `PriceHistory`: OHLCV 1H/1D/1W.
- `MarketIndex`: índice por tipo, volumen y variación.
- `Notification`: enumera volatilidad, batalla, incubación, entrenamiento y sistema; hoy los productores activos crean principalmente alertas de volatilidad y resultados de batalla.

Servicios:

- `PricingEngine`: rareza + rendimiento de últimas batallas + presión buy/sell 24 h; persiste snapshots y delega control de volatilidad.
- `TradingEngine`: market buy/sell, reserva de fondos BUY limit, ejecución/cancelación de limit orders y actualización de portfolio.
- `MarketIndicesService`: hoy calcula promedio simple de precios por tipo y registra el volumen por separado, aunque sus docstrings declaran ponderación por volumen.
- `VolatilityService`: corte por cambio mayor a 10 %, halt de 15 minutos y notificaciones a holders.

Tasks periódicas:

- precios cada minuto;
- limit orders cada 30 segundos;
- snapshots horarios y diarios;
- índices diarios;
- actualización puntual post-batalla.

Views:

- mercado/detalle, colocación/cancelación de órdenes, portfolio y órdenes;
- índices y notificaciones;
- APIs JSON de price history, índices, notificaciones y portfolio summary;
- `_compute_income_expenses()` alimenta portfolio y dashboard.

Rutas bajo `/trading/`: `market/`, `creature/<uuid>/`, `order/place/`, `order/<uuid>/cancel/`, `portfolio/`, `orders/`, `indices/`, `notifications/` y `api/...`.

### `dashboard` y UI

- `dashboard.views.dashboard` agrega transacciones, portfolio, P&L, distribución e income/expenses.
- Depende actualmente de `trading.views._compute_income_expenses`; cambiar ese contrato afecta ambas apps.
- `templates/layout.html` define tema dark/light, navegación, CSS variables, footer y scripts CDN.
- Hay 23 templates, incluidas páginas de batallas y el partial `creature/includes/battle_card.html`.
- Chart.js consume JSON y el portfolio refresca su resumen por polling.
- No existe pipeline local de assets estáticos; no introducir uno en un cambio no relacionado.

## 3. Flujos críticos

### Compra market

`place_order_view` → `TradingEngine.execute_market_buy` → lock de account → débito subtotal+1.5 % → upsert/average de portfolio → `Order` + `Trade`.

### Venta market

`place_order_view` → `execute_market_sell` → locks → validación de holdings → crédito subtotal-comisión → reducción/eliminación de portfolio → `Order` + `Trade`.

### Orden limit

- BUY reserva al colocar precio×cantidad+comisión estimada.
- Celery Beat evalúa cruces de precio y ejecuta bajo lock.
- Cancelar libera fondos BUY pendientes.
- Cambiar reservas o matching exige tests de doble ejecución, cancelación y concurrencia.

### Transferencias

Managers de `Transfer` validan, abren transacción, bloquean cuentas, actualizan centavos y persisten estado. El banco externo es un borde sustituible, no lógica de dominio.

### Incubación y entrenamiento

- La operación HTTP llama al servicio.
- El servicio cobra atómicamente y agenda Celery.
- La task aplica el efecto diferido.
- Riesgo actual: el scheduling ocurre después del bloque de débito; un fallo de Redis puede dejar un cobro sin ejecución. Cualquier cambio debe cubrir compensación e idempotencia con tests.

### Batallas

Beat elige dos criaturas disponibles, `BattleService` crea snapshots y la task encadena turnos. Al finalizar se actualizan ELO/stats/precios y notificaciones. ELO nunca forma parte de contratos públicos.

## 4. Invariantes que no se negocian

### Precisión financiera

- Saldo persistido: `BigIntegerField` en centavos.
- Precios/dinero: `DecimalField(max_digits=12, decimal_places=2)` y `Decimal`.
- Fracciones: `DecimalField(max_digits=18, decimal_places=8)`.
- No usar `FloatField` ni `float` en persistencia o cálculo financiero.
- Convertir a número de presentación solo en el último borde si el contrato JSON existente lo requiere.
- Construir `Decimal` desde strings y cuantizar redondeos monetarios explícitamente.

### Atomicidad y concurrencia

- Toda mutación de saldo, orden, portfolio, incubación o entrenamiento usa `transaction.atomic()`.
- Reconsultar dentro del bloque con `select_for_update()`; no confiar en instancias cargadas antes.
- Adquirir locks en orden estable y mantener el bloque pequeño.
- Una excepción debe revertir el conjunto completo de cambios.
- Concurrencia real se prueba con PostgreSQL y `TransactionTestCase`, no SQLite.

### Asincronía

- Tasks finas: cargar/adaptar y delegar a servicios.
- Services deben ser ejecutables sin worker para poder probarlos.
- Reintentos requieren idempotencia persistente; no alcanza con capturar excepciones.
- Nunca duplicar hatch, boost, resultado de batalla, notificación o ejecución de orden.
- Solo una instancia de Celery Beat por entorno.

### Capas y contratos

- Forms/serializers validan shape de entrada.
- Views gestionan auth, HTTP, mensajes, status, redirects y contexto.
- Services coordinan negocio y transacciones.
- Models/managers expresan invariantes persistentes y consultas de dominio.
- Tasks y commands orquestan, no duplican negocio.
- Preservar nombres de URL, keys JSON y variables de template salvo cambio explícito.
- Mantener `Trade` y snapshots como hechos históricos; no recalcularlos con reglas nuevas.

### Datos y esquema

- No editar ni borrar migraciones compartidas; agregar migraciones nuevas.
- No modificar `seed_data` ni ejecutar `seed_all --clear` salvo que el objetivo lo requiera.
- No cambiar `AUTH_USER_MODEL`, unidades financieras, UUIDs o relaciones históricas como limpieza lateral.
- No exponer credenciales, ELO ni información interna en templates/API/logs.

## 5. Áreas de alto riesgo / no tocar lateralmente

- `TradingEngine`, `TransferManager`, `IncubationService` y `TrainingService`: dinero y locks.
- `BattleService.process_battle_result` y cadena `process_battle_turn`: efectos repetibles y pricing.
- `PricingEngine`/`VolatilityService`: compounding, circuit breaker y alertas.
- Estados y vínculos de `Order`, `Trade`, `Portfolio`, `Battle` e `Incubation`.
- Migraciones existentes, datos seed y contratos de templates/Chart.js.
- Nombres `web`, `worker`, `beat` del `Procfile`.
- `brokerdex/celery.py` es la app canónica; `creature/celery.py` es un duplicado legado y no debe convertirse en un segundo runtime.

Si una tarea exige tocar estas áreas, primero crear un test de caracterización/regresión.

## 6. Deuda conocida: verificar, no perpetuar

- Settings están hardcodeados y orientados a desarrollo.
- `Procfile` usa Django `runserver`; no es un despliegue web de producción.
- `run.sh` solo inicia Redis en Docker y `runserver`; no levanta worker ni beat.
- `brokerdex/.my_pgpass` está trackeado: tratarlo como material sensible y no mostrar/copiar su contenido.
- Backend email/username existe pero no está registrado en settings.
- `pyproject.toml` apunta mypy-django a `myproject.settings`, nombre ajeno al proyecto.
- Persisten conversiones a `float` en propiedades/views de presentación y algunos tests crean precios desde literales float.
- La idempotencia está declarada como requisito, pero no todos los efectos Celery tienen una clave/estado de deduplicación robusto.
- Algunas reglas de negocio viven todavía en models/tasks o se comparten mediante funciones privadas de views.
- `PARTIALLY_FILLED` existe en el modelo, pero el motor actual no produce fills parciales.
- `CreatureSerializer` no tiene rutas DRF y referencia campos inexistentes (`symbol`, `original_image_url`).
- `seed_market` duplica parcialmente al seeder canónico `seed_all`.
- `WithdrawForm` referencia `Account.balance`, atributo inexistente; las views bancarias carecen de cobertura HTTP.
- `generate_random_battle` está obsoleto: importa símbolos inexistentes y referencia atributos no definidos.
- `makemigrations --check --dry-run` detecta una migración pendiente para `BattleParticipant.Meta.ordering` (`0007_alter_battleparticipant_options`).
- Los tests de integración de `seed_all` escriben sprites en `MEDIA_ROOT`; hoy pueden dejar archivos no trackeados y deben aislarse/limpiarse.

No corregir esta deuda fuera de alcance. Si bloquea una implementación, cubrir primero el comportamiento y corregir el mínimo necesario.

## 7. Refactor obligatorio

Después de hacer verde una implementación:

1. aplicar `.cursor/skills/semantic-compression/SKILL.md`;
2. aplicar `.cursor/skills/continuous-granularity/SKILL.md`;
3. conservar acceso a primitivas ORM/transaccionales;
4. evitar repositorios genéricos, jerarquías prematuras, DTOs duplicados, signals ocultos y god services;
5. volver a ejecutar tests.

Las abstracciones nacen de dos o más casos reales. Código más corto que oculta locks, unidades o efectos asíncronos es una regresión.

## 8. Testing y operación

La suite usa Django `TestCase` y archivos `tests.py`/`tests_integration.py`. Celery se parchea en el punto de uso; el flujo normal de tests no necesita Redis vivo.

Cobertura fuerte: trading market, pricing, modelos, incubación y batallas. Prioridades pendientes: ejecución periódica de limit orders, tasks de Beat, concurrencia real, dashboard, auth, views bancarias, income/expenses y `portfolio_summary_api`.

```bash
uv sync
uv run python manage.py check
uv run python manage.py test trading creature banking account dashboard
uv run python manage.py makemigrations --check --dry-run
uv run honcho start
```

Seeding:

```bash
uv run python manage.py seed_all
uv run python manage.py seed_all --demo
uv run python manage.py add_funds <username> <amount_en_centavos>
```

Consultar `cursorrules.md` y `.cursor/skills/django-tdd/SKILL.md` para el ciclo completo, selección de tests y gestión de PostgreSQL/Redis/Procfile.
