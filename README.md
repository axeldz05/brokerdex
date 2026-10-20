# Brokerdex

Brokerdex es una plataforma de trading gamificada donde las criaturas son
activos fraccionables. Las órdenes, batallas, incubaciones y entrenamientos
afectan hechos financieros persistidos y se procesan con Django y Celery.

## Stack actual

- Python 3.13 administrado exclusivamente con `uv` y `uv.lock`.
- Django 5.2.x sobre PostgreSQL; SQLite no es un motor soportado.
- Celery 5.6.x con Redis como broker y result backend.
- Django Templates, Bootstrap 5, Chart.js y el plugin financiero de Chart.js.
- Gunicorn y WhiteNoise para el proceso web y los estáticos de producción.
- Usuario propio `account.Account`, con login por username o email.

## Inicio con Docker (entorno aislado)

No requiere instalar Python, uv, PostgreSQL ni Redis en el sistema anfitrión:

```bash
docker compose up
# o simplemente:
bash run.sh
```

El stack de Docker levanta automáticamente:
- `db`: PostgreSQL 17 Alpine con volumen persistente (`postgres_data`).
- `redis`: Redis Alpine con volumen persistente (`redis_data`).
- `migrate`: Aplica migraciones pendientes y carga el catálogo inicial si la base está vacía.
- `web`: Servidor Django accesible en `http://localhost:8000`.
- `worker`: Celery worker para tareas asíncronas.
- `beat`: Celery beat scheduler.

Para correrlo localmente sobre el sistema operativo anfitrión sin Docker:

```bash
bash run.sh --local
```

## Inicio local

Requisitos: Python 3.13, `uv`, PostgreSQL y Redis. Ajustar las URLs locales del
archivo antes de usarlo si los nombres de rol o base difieren:

```bash
cp .env.example .env
uv sync --locked
pg_isready -h localhost -p 5432
redis-cli -u redis://localhost:6379/0 ping
uv run python manage.py migrate
uv run python manage.py seed_all --demo
bash run.sh
```

`run.sh` valida Django, PostgreSQL y Redis y luego inicia una sola topología de
desarrollo desde `Procfile.dev`: `runserver`, un worker y un Beat. No iniciar un
segundo Beat contra la misma cola.

Los seeds base pueden ejecutarse sin demo:

```bash
uv run python manage.py seed_all
```

`seed_all --clear --demo` borra datos y solo debe usarse con confirmación
explícita en una base local descartable. El seed puede descargar sprites desde
PokeAPI.

### Población Masiva y Simulación de Mercado

Brokerdex incluye un generador de población a gran escala y un motor de bots autónomos para simular un mercado financiero vivo:

1. **Poblar el mercado masivo**:
   ```bash
   uv run python manage.py seed_all --simulation
   # o reiniciando la base:
   uv run python manage.py seed_all --clear --simulation
   ```
   Crea 100 entrenadores, carteras distribuidas, 30 días de historial de velas OHLCV (1H, 1D, 1W), trades pasados, incubaciones y combates con shocks ELO.

2. **Monitoreo en vivo de bots (Stream interactivo)**:
   ```bash
   uv run python manage.py run_bots
   # o con intervalo configurable (ej. cada 2 segundos):
   uv run python manage.py bots --stream --interval 2.0
   ```

3. **Control de autonomía en tiempo real**:
   ```bash
   uv run python manage.py bots --status   # Muestra métricas por estrategia
   uv run python manage.py bots --stop     # Pausa la autonomía de los bots
   uv run python manage.py bots --start    # Reanuda la autonomía
   ```

Estrategias de bots implementadas:
- `[MOMENTUM]`: Sigue rachas de victorias en combates y presiones de 24h.
- `[VALUE]`: Caza descuentos o toma ganancias según el precio de referencia intrínseco.
- `[MARKET_MAKER]`: Provee liquidez colocando órdenes límite alrededor del spread.
- `[CASUAL]`: Compras esporádicas y apertura de incubaciones.

## Conducta implementada

- El libro de órdenes aplica prioridad precio-tiempo, fills P2P parciales,
  reservas de efectivo/tenencias y Market Maker únicamente para el remanente.
- La comisión histórica de cada fill es 1,5 %; saldos se guardan en centavos,
  precios en `Decimal(12,2)` y cantidades en `Decimal(18,8)`.
- El resultado de batalla persiste un shock simétrico derivado de ELO en
  `pricing_reference_price`. El rating ELO y sus snapshots siguen siendo
  internos; la UI solo muestra el impacto porcentual.
- Incubación y entrenamiento tienen sesiones persistentes, recuperación de
  scheduling y efectos idempotentes. Un entrenamiento que termina en fallo
  permanente devuelve su costo una sola vez.
- El historial de precios genera velas OHLCV del último bucket UTC completo para
  1H, 1D y 1W. La UI muestra candlesticks, volumen y una tabla accesible.
- Los índices por tipo ponderan el precio actual por volumen histórico de
  trades; si no existe volumen usan promedio simple.
- El dashboard arma un ledger firmado desde `Transfer` y `Trade`, incluidos
  depósitos, retiros, transferencias, compras, ventas y comisiones históricas.
- Cada cuenta creada por el manager recibe CVU de 22 dígitos y alias únicos. El
  depósito genérico acepta CVU o alias mediante webhook HMAC-SHA256 e
  idempotencia persistente por referencia del proveedor.

Las rutas bancarias canónicas son `/transfer/`, `/withdraw/`, `/deposit/` e
`/invest/`. Las variantes antiguas bajo `/banking/` redirigen permanentemente.

## Arquitectura

```text
account/       identidad, autenticación, CVU/alias y saldo
banking/       transferencias, depósitos y retiros
creature/      criaturas, batallas, incubación y entrenamiento
dashboard/     resumen financiero y ledger
trading/       órdenes, fills, portfolio, precios, índices y alertas
brokerdex/     settings por ambiente, URLs, observabilidad y Celery
templates/     UI server-rendered
seed_data/     catálogo y datos demo
```

Views y forms traducen HTTP; services coordinan negocio y transacciones;
models/managers expresan persistencia; tasks y commands delegan. Las
invariantes completas para agentes están en [AGENTS.md](AGENTS.md).

## Verificación

La suite normal usa PostgreSQL y sustituye los bordes externos; no necesita un
worker ni Redis vivos.

```bash
uv sync --locked
uv run flake8
DJANGO_SETTINGS_MODULE=brokerdex.settings.test uv run python manage.py check
DJANGO_SETTINGS_MODULE=brokerdex.settings.test uv run python manage.py makemigrations --check --dry-run
DJANGO_SETTINGS_MODULE=brokerdex.settings.test uv run python manage.py test
```

GitHub Actions ejecuta esos gates con PostgreSQL 17 y Redis 7 y añade
`check --deploy --fail-level WARNING` con settings de producción.

## Operación y seguridad

- [Entornos, despliegue, health checks y gates](docs/operations.md)
- [Contrato del webhook de depósitos](docs/deposit-webhook.md)
- [Smoke operativo de Celery](docs/celery-smoke.md)
- [Retiro de credenciales y rotación](docs/security/credential-rotation.md)
