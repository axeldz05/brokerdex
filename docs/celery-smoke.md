# Celery operational smoke

Ejecutar este check cuando Redis y un worker Celery estén disponibles. No forma
parte de la suite Django ni de CI y no inicia Celery Beat. Complementa
`/health/ready/`: readiness prueba Redis, pero no que un worker consuma.

## Local

Preparar PostgreSQL/Redis y arrancar un worker con los mismos settings y
`REDIS_URL` que usará el comando:

```bash
redis-cli -u redis://localhost:6379/0 ping
uv run celery -A brokerdex worker --loglevel=info
```

En otra terminal:

```bash
uv run python manage.py celery_smoke --timeout 10
```

Usar `--queue <name>` solo cuando el worker consume una cola no default. El
worker debe arrancar con esa cola. No iniciar Beat para el smoke.

## Producción

Ejecutar dentro del mismo entorno privado del proceso, sin imprimir variables:

```bash
DJANGO_SETTINGS_MODULE=brokerdex.settings.production uv run python manage.py celery_smoke --timeout 10
```

El worker destino también debe usar `brokerdex.settings.production` y la misma
`REDIS_URL`. No pasar URLs con credenciales como argumentos ni habilitar
tracing de shell.

## Qué valida

El command:

1. comprueba que broker y result backend estén configurados;
2. publica la task sin efectos de dominio `celery.accumulate` con token único;
3. espera el resultado hasta el timeout;
4. verifica que vuelva el mismo token;
5. elimina el resultado.

El éxito informa solo los esquemas del broker y backend; nunca imprime URLs ni
credenciales.

No valida Beat, schedules, PostgreSQL ni una task de negocio concreta. Cuando
un cambio afecta incubación, training, batallas, retiros u órdenes, ejecutar
además sus tests de servicio/task y observar una ejecución controlada del flujo
relevante.

## Fallos

- `broker is not configured` o `result backend is not configured`: revisar el
  módulo de settings y variables inyectadas.
- timeout o error de conexión: revisar Redis, cola y worker.
- payload inesperado: detener el rollout y revisar routing/serialización.
- fallo de cleanup después de un round-trip correcto: revisar el result backend
  antes de repetir masivamente.

El comando devuelve código distinto de cero mediante `CommandError`. La
configuración completa y los demás gates están en
[Operación de Brokerdex](operations.md).
