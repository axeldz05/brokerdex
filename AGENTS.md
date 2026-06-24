# Brokerdex: AI Agent Context & Development Rules

Este documento es la referencia única de arquitectura, convenciones y restricciones de Brokerdex. **Debe ser usado por el agente al inicio de cada sesión para entender el proyecto sin re-explorar.**

---

## 1. Domínio y Stack Tecnológico

**Filosofía:** Brokerdex es una plataforma de trading gamificada donde Pokémon = activos financieros. Sistema P2P de emparejamiento (Market Maker como contraparte cuando no hay liquidez). Propiedad fraccional permitida.

**Stack:**
- **Backend:** Django 5.1 + PostgreSQL (`brokerdex_db`, usuario `brokerdex_user`)
- **Async:** Celery con Redis `redis://localhost:6379/0`
- **Frontend:** Django Templates + Bootstrap 5 (CDN) + Chart.js (CDN) + Boxicons/Bootstrap Icons
- **Auth:** Custom user model (`Account` basado en `AbstractBaseUser`), login con email o username

---

## 2. Estructura del Proyecto

```
brokerdex-django/
  account/           # Auth, perfiles, balance
  banking/           # Transferencias, depósitos, retiros
  creature/          # Pokémon, batallas, incubación, entrenamiento
  dashboard/         # Dashboard principal
  trading/           # Mercado, portfolio, órdenes, trades, precios
  templates/         # Layout base, landing page
  brokerdex/         # Settings, URLs raíz, Celery app
  seed_data/         # Datos iniciales JSON
```

### 2.1 App: `account` — Usuarios y Balance
- **Modelo:** `Account(AbstractBaseUser)` — `email`, `username`, `balance_cents` (BigIntegerField), `cvu`, `alias`
- **Managers:** `AccountManager` — `create_user`, `create_superuser`
- **Backends:** `EmailOrUsernameModelBackend` — login con email o username
- **Properties:** `balance_dollars` (getter/setter), `balance_audit` (suma transfers)
- **Views:** `log_in`, `register`, `log_out`, `settings` (secciones: general, privacy, account, theme, bank_accounts)
- **URLs:** `/account/login/`, `/register/`, `/logout/`, `/settings/`

### 2.2 App: `banking` — Movimientos de Dinero
- **Modelo:** `Transfer` — `exchange_id` (ShortUUID "TRN..."), `type` (TRANSFER/DEPOSIT/WITHDRAW), `status` (pending/completed/failed), `sender`, `receiver`, `amount` (BigIntegerField, cents), `date`, `description`
- **Manager:** `TransferManager` — `process_external_deposit`, `process_external_withdrawal`, `create_transaction`
- **Servicio:** `ExternalBankService` — stub que siempre retorna `True`
- **Forms:** `TransferForm`, `WithdrawForm`
- **Views:** `transfer`, `withdraw`, `invest`, `deposit`
- **URLs:** `/transfer/`, `/withdraw/`, `/deposit/`, `/invest/`

### 2.3 App: `creature` — Pokémon y Juego
#### Modelos (441 líneas):
- **`Creature`** — `id` (UUID), `name`, `description`, `type` (PrimaryType: 18 tipos), `secondary_type`, `current_price` (Decimal 12,2), `previous_close`, 6 stats (hp/attack/defense/sp_atk/sp_def/speed, PositiveIntegerField), `abilities` (M2M→Ability), `evolves_from`, `is_legendary`, `is_mythical`, `circuit_breaker_expires_at`, `battle_cooldown`, `cooldown_expires_at`, `currently_in_battle`, `small_icon`, `large_icon`
- **`Ability`** — `name`, `ability_type` (PrimaryType), `power`, `accuracy`, `pp`, `priority`, `damage_class` (physical/special/status), `target`, healing, status effects
- **`Battle`** — `status` (pending/active/finished), `current_turn`, `winner`, `battle_log` (JSON)
- **`BattleParticipant`** — `current_hp`, `attack_stage`, `defense_stage`, `status_ailment`
- **`BattleAction`** — `turn_number`, `actor`, `target`, `ability`, `damage_dealt`
- **`EggTemplate`** — `price` (Decimal), `hatch_duration` (DurationField), `creature_pool` (M2M→Creature)
- **`Incubation`** — `status` (INCUBATING/HATCHED), `hatches_at`, `hatched_creature`, `celery_task_id`
- **Signal:** `limit_creature_abilities` — max 4 abilities por criatura

#### Views (115 líneas):
- `incubation_shop_view` — lista eggs + incubaciones activas
- `purchase_egg_view` — POST, compra egg, maneja errores de Celery/Redis
- `incubation_status_view` — todas las incubaciones del usuario
- `train_creature_view` — POST, inicia entrenamiento, captura ConnectionError para Redis

#### Services (140 líneas):
- `IncubationService.purchase_egg()` — descuenta balance con `select_for_update`, agenda `hatch_egg` con ETA
- `IncubationService.hatch_egg()` — selecciona criatura random del pool, crea Portfolio entry
- `TrainingService.get_training_cost()` — 10% del precio de mercado
- `TrainingService.train_creature()` — descuenta balance, agenda `complete_training` con countdown=30s

#### Tasks (127 líneas, Celery shared_task):
- `matchmake_random_battle` — cada 5 min, 2 criaturas random
- `process_battle_turn` — recursivo cada 10s, cálculo daño = (power * attack) / defense
- `hatch_egg` — ejecuta `IncubationService.hatch_egg`
- `complete_training` — boost random stat (+1~5), recalcula precio via PricingEngine

#### URLs:
- `/incubation/`, `/incubation/purchase/<uuid:egg_id>/`, `/incubation/status/`, `/training/<uuid:portfolio_id>/`

### 2.4 App: `trading` — Mercado, Portfolio, Órdenes
#### Modelos (356 líneas):
- **`Portfolio`** — `owner`→Account, `creature`→Creature, `quantity` (Decimal 18,8), `average_cost` (Decimal 12,2). Properties: `current_value`, `cost_basis`, `unrealized_pnl`, `unrealized_pnl_pct`
- **`Order`** — `order_type` (BUY/SELL), `execution_type` (MARKET/LIMIT), `quantity`, `limit_price`, `filled_quantity`, `status` (OPEN/FILLED/PARTIALLY_FILLED/CANCELLED). Property: `remaining_quantity`
- **`Trade`** — `buyer`→Account, `seller`→Account (nullable = Market Maker), `creature`, `quantity`, `price_per_unit`, `commission` (1.5%), `executed_at`. Property: `total_amount`
- **`PriceHistory`** — OHLCV data, `interval` (1H/1D/1W), `timestamp`
- **`MarketIndex`** — índice por tipo, `value`, `previous_value`, `change_pct`
- **`Notification`** — `type` (VOLATILITY/BATTLE/INCUBATION/TRAINING/SYSTEM), `is_read`

#### Views (536 líneas):
- `market_view` — lista criaturas con precios y cambios
- `creature_detail_view` — detalle + gráfico + formularios compra/venta
- `place_order_view` — POST, ejecuta market/limit orders
- `portfolio_view` — holdings, P&L, distribución, income/expenses
- `order_history_view` — historial con filtro por status
- `cancel_order_view` — cancela limit order
- `price_history_api` — JSON OHLCV para Chart.js
- `market_indices_view` / `market_indices_api`
- `notifications_view` / `notifications_api`
- `portfolio_summary_api` — JSON para auto-refresh (balance, portfolio, IE data)
- `_compute_income_expenses(user, period)` — función privada que agrupa trades+transfers por mes/año, retorna income_data/expenses_data/net_data con breakdown por sales/deposits/transfers_in/purchases/withdrawals/transfers_out/commissions

#### Services (726 líneas):
- `PricingEngine` — precio = base × (1 + Δ_batallas + Δ_mercado) × rarity_mult
  - `calculate_battle_delta()` — win rate últimos 20 battles, ±10%
  - `calculate_market_delta()` — buy/sell volume ratio 24h, ±5%
  - `get_rarity_multiplier()` — normal 1.0, legendary 1.5, mythical 2.0
  - `update_creature_price()` — guarda y chequea volatilidad
  - `record_price_snapshot()` — OHLCV por intervalo
- `TradingEngine` — motor de ejecución:
  - `execute_market_buy/sell()` — débito/crédito con `select_for_update`, actualiza Portfolio
  - `place_limit_order()` — reserva fondos para BUY, verifica holdings para SELL
  - `check_limit_orders()` — ejecuta órdenes cuando precio cruza umbral
  - `_execute_limit_buy/sell()` — ejecución atómica
  - `cancel_order()` — libera fondos reservados
- `MarketIndicesService` — calcula índices ponderados por precio
- `VolatilityService` — circuit breaker si precio cambia >10%

#### Tasks (85 líneas):
- `update_creature_prices` — cada 1 minuto, recalcula todos los precios
- `check_pending_limit_orders` — cada 30s
- `record_hourly/daily_price_snapshots` — OHLCV
- `calculate_market_indices` — diario
- `update_price_after_battle` — on-demand

#### URLs:
- `/trading/market/`, `/trading/portfolio/`, `/trading/orders/`
- `/trading/creature/<uuid:creature_id>/`, `/trading/order/place/`, `/trading/order/<uuid:order_id>/cancel/`
- `/trading/indices/`, `/trading/notifications/`
- `/trading/api/price-history/<uuid:creature_id>/`, `/trading/api/indices/`, `/trading/api/notifications/`, `/trading/api/portfolio-summary/`

#### Template Tags (`templatetags/trading_tags.py`):
- `type_color(type_name)` — color hex del tipo Pokémon
- `type_bg(type_name)` — fondo semi-transparente

### 2.5 App: `dashboard` — Panel Principal
- **View:** `dashboard()` — portfolio summary, type distribution, income/expenses, transaction history
- **Template:** `dashboard/dashboard.html` — balance, investment chart (pie), transaction list, Income & Expenses card
- **URL:** `/dashboard/`

### 2.6 App: `brokerdex` — Configuración Central
- **settings.py:** Celery broker en Redis, Celery Beat schedule con 6 tareas periódicas
- **celery.py:** `app = Celery('brokerdex')`, `app.autodiscover_tasks()`
- **urls.py:** Raíz: `sw.js`, include de banking/account/dashboard/creature/trading, `admin/`
- **views.py:** `index()` (redirect a dashboard si autenticado), `sw_js()` (service worker no-op)

---

## 3. Sistema de Templates

### Layout base: `templates/layout.html`
- Tema oscuro por defecto con CSS variables globales (data-theme="dark"/"light")
- Variables: `--bg-primary`, `--bg-secondary`, `--bg-card`, `--border-color`, `--text-primary`, `--text-secondary`, `--accent-green/red/blue/purple`
- Navbar con links a Dashboard/Market/Portfolio/Orders/Indices/Alerts + botón tema + dropdown Profile
- Footer, back-to-top, theme toggle JS (localStorage "brokerdex-theme")
- Bootstrap 5.3 CSS/JS, Boxicons, Bootstrap Icons (todos CDN)
- Bloques: `{% block content %}`, `{% block extra_js %}`
- Overrides de `.table`, `.btn-outline-*`, `.dropdown-menu` para tema oscuro

### Templates por ruta (20 templates de proyecto):

| Template | Propósito |
|----------|-----------|
| `templates/index.html` | Landing page (público) |
| `templates/home.html` | Home page (público) |
| `account/templates/account/login.html` | Login |
| `account/templates/account/register.html` | Registro |
| `account/templates/account/settings.html` | Settings con tabs (general, privacy, account, theme, bank_accounts) |
| `dashboard/templates/dashboard/dashboard.html` | Dashboard principal |
| `banking/templates/transfer.html` | Transferencia entre usuarios |
| `banking/templates/withdraw.html` | Retiro externo |
| `banking/templates/deposit.html` | Depósito externo |
| `banking/templates/invest.html` | Invertir (listado completo) |
| `trading/templates/trading/market.html` | Market (tabla criaturas con búsqueda) |
| `trading/templates/trading/creature_detail.html` | Detalle criatura (gráfico, stats, buy/sell, limit order) |
| `trading/templates/trading/portfolio.html` | Portfolio (holdings, detail panel toggle, donut chart, income/expenses con gráfico mensual/anual, balance auto-refresh) |
| `trading/templates/trading/orders.html` | Historial de órdenes |
| `trading/templates/trading/market_indices.html` | Índices por tipo |
| `trading/templates/trading/notifications.html` | Alertas del usuario |
| `creature/templates/creature/incubation_shop.html` | Tienda de eggs |
| `creature/templates/creature/incubation_status.html` | Estado de incubaciones |

### Patrones JS comunes:
- Chart.js para gráficos (line/doughnut/pie/bar, siempre desde CDN)
- fetch() para APIs de price-history y portfolio-summary
- Auto-refresh cada 15s en portfolio (balance + summary cards + IE chart)

---

## 4. Reglas de Desarrollo (Innegociables)

### Precisión Financiera
- **PROHIBIDO** `FloatField` o `float` para precios/balances/cantidades
- **OBLIGATORIO:** `DecimalField` (Django) + `decimal.Decimal` (Python)
- Dinero/precios: `max_digits=12, decimal_places=2`
- Fracciones de activos: `max_digits=18, decimal_places=8`
- Balance: `BigIntegerField` en cents (`balance_cents`)

### Concurrencia
- `transaction.atomic()` + `.select_for_update()` para:
  - Modificar balances (Account)
  - Ejecutar trades (Order + Portfolio)
  - Comprar eggs / entrenar
- Siempre re-fetch el objeto dentro del bloque atómico

### Idempotencia Celery
- Tareas de battles, incubation, training deben ser idempotentes
- Usar `task_id` o hash del evento para dedup
- No duplicar rewards ni aplicar racha de victorias dos veces

### Arquitectura de Capas
- **Views:** validación de entrada (forms/serializers), HTTP status, redirección
- **Services:** toda la lógica de negocio pesada
- **Tasks:** solo llaman a services

### API y Gráficos
- Endpoints de series temporales: `[{time: "ISO_TIMESTAMP", price: "250.50"}]`
- Alertas de volatilidad (>10%) → `Notification` en DB

---

## 5. Flujo de Datos Clave

### Compra Market:
`POST /trading/order/place/` → `TradingEngine.execute_market_buy()` → descuenta balance (cents) + commission 1.5% → crea Portfolio/update weighted avg → registra Trade y Order → redirect a creature_detail

### Venta Market:
Análogo pero acredita balance neto (subtotal - commission) y reduce/delega Portfolio

### Entrenamiento:
POST `/training/<portfolio_id>/` → `TrainingService.train_creature()` → descuenta 10% price → agenda Celery task 30s → `complete_training` boostea stat random (+1~5) y recalcula precio

### Incubación:
POST `/incubation/purchase/<egg_id>/` → `IncubationService.purchase_egg()` → descuenta egg.price → agenda Celery con ETA → `hatch_egg` crea Portfolio entry con criatura random del pool

### Income & Expenses:
`_compute_income_expenses(user, period)` agrupa por mes/año:
- Income: sell proceeds (Trade.seller) + DEPOSIT (Transfer) + TRANSFER received
- Expenses: buy cost (Trade.buyer) + WITHDRAW (Transfer) + TRANSFER sent + commissions (all trades)
- Ambos lados se consumen en portfolio y dashboard via `portfolio_summary_api`

---

## 6. Configuración Local

```bash
# Redis
redis-server

# Celery worker
celery -A brokerdex worker --loglevel=info

# Celery beat
celery -A brokerdex beat --loglevel=info

# Django dev server
python manage.py runserver

# Seed data
python manage.py seed_all

# Tests
python manage.py test trading creature
```

### Seed data:
- `python manage.py seed_all` — carga criaturas desde `seed_data/pokemon_data.json`, crea usuarios demo, precios iniciales, órdenes demo
- `python manage.py generate_random_battle` — inicia una battle entre 2 criaturas
- `python manage.py add_funds` — agrega fondos a un usuario
