# Brokerdex

A gamified trading platform where Pokémon are financial assets. Buy, sell, and hold shares of creatures while they battle automatically, affecting their market valuation through an ELO-based pricing engine.

## Stack

- **Backend:** Django 5.1 + PostgreSQL
- **Async:** Celery + Redis (broker/backend)
- **Frontend:** Django Templates + Bootstrap 5 (CDN) + Chart.js (CDN)
- **Auth:** Custom user model (`Account`) with email/username login

## Quick Start

```bash
redis-server &
celery -A brokerdex worker --loglevel=info &
celery -A brokerdex beat --loglevel=info &
python manage.py runserver
python manage.py seed_all --demo
```

## Features

### Market (Order Book)
- Buy/sell creatures via market orders (instant) or limit orders (triggered at target price)
- 1.5% commission on all trades
- Weighted-average cost tracking per portfolio entry
- Automated limit order matching every 30 seconds via Celery Beat

### Dynamic Pricing Engine
- **Formula:** `price = base × (1 + Δ_battles + Δ_market) × rarity_multiplier`
- **Δ_battles:** win rate from last 20 battles, ±10% max swing
- **Δ_market:** buy/sell volume ratio over 24h, ±5% max swing
- **Rarity multiplier:** normal 1.0, legendary 1.5, mythical 2.0
- Prices recalculate every minute via Celery Beat

### Battle System
- Auto-matches 2 available creatures every 5 minutes via Celery Beat
- Turn-based combat: 3-minute intervals between turns (configurable via `BATTLE_TURN_INTERVAL`)
- ELO-based valuation impact (K=32, hidden from users):
  - Underdog win → large price increase (+~5%)
  - Favorite loss → large price decrease (−~5%)
- Battle cards with live HP bars, countdown timer, and per-turn action flash
- Per-turn investment tracking (shows how many people bought during each turn)
- Full turn-by-turn timeline with damage, status effects, and investments
- Creature leaderboard by win/loss record

### Incubation
- Purchase eggs → timed hatch (Celery ETA scheduling) → random creature from pool → portfolio credit
- 3 egg tiers: Basic, Premium, Legendary

### Training
- Pay 10% of creature's market price → 30s Celery countdown → +1–5 random stat boost → price recalculation

### Volatility Alerts
- If price moves >10% in a single recalculation → circuit breaker halts trading for 15 minutes + notifications to all holders

### Market Indices
- Type-based indices (Fire, Water, etc.) — volume-weighted average price
- Calculated daily via Celery Beat

### Portfolio Analytics
- Holdings with unrealized P&L, cost basis, asset distribution
- Income/expenses chart: monthly/yearly breakdown by sales, deposits, transfers, commissions
- 15-second auto-refresh on portfolio page

### Price Charts
- OHLCV candlestick data at 1H/1D/1W intervals
- Chart.js interactive chart on creature detail page

## Seeding

```bash
python manage.py seed_all                 # creatures, abilities, eggs
python manage.py seed_all --demo          # + users, orders, 3 finished battles, 2 active battles, incubations
python manage.py seed_all --clear --demo  # reset + full seed
```

Sprites (small icon + official artwork) are auto-downloaded from PokeAPI during seeding.

## Architecture

```
brokerdex-django/
├── account/        # Auth, profiles, balance (cents via BigIntegerField)
├── banking/        # Transfers, deposits, withdrawals (stubbed external bank)
├── creature/       # Pokémon, battles, incubation, training
├── trading/        # Market, portfolio, orders, trades, pricing engine
├── dashboard/      # Main dashboard
├── brokerdex/      # Settings, URLs, Celery app
└── seed_data/      # JSON files for abilities, creatures, demo data
```

### Layer Separation
- **Views:** input validation, HTTP status, redirects
- **Services:** all business logic (PricingEngine, TradingEngine, BattleService, etc.)
- **Tasks (Celery):** only call services — no business logic

### Celery Beat Schedule
| Task | Interval |
|------|----------|
| Matchmake random battles | every 5 min |
| Update creature prices | every 1 min |
| Check pending limit orders | every 30s |
| Record hourly price snapshots | :00 each hour |
| Record daily price snapshots | midnight |
| Calculate market indices | 00:30 daily |

### Concurrency
- `transaction.atomic()` + `.select_for_update()` for balance changes, trades, egg purchases, training
- Celery tasks are idempotent (battles, incubation, training) — no double-application of rewards

### Precision
- Money: `BigIntegerField` in cents (balance), `DecimalField(12,2)` for prices
- Share quantities: `DecimalField(18,8)`
- No `FloatField` or `float` for any financial value
