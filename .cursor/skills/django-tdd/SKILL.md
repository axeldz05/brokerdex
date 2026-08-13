---
name: django-tdd
description: Implementa y refactoriza Brokerdex con TDD optimizado para Django, PostgreSQL y Celery: ciclos red-green-refactor estrechos, selección correcta de TestCase, aislamiento de Redis y verificación específica de dinero y concurrencia. Usar para toda corrección o funcionalidad.
---

# TDD optimizado para Brokerdex

## Ciclo obligatorio

1. **Red:** escribir el test observable más pequeño que falle por la razón esperada.
2. Ejecutar solo ese test y comprobar el fallo.
3. **Green:** implementar la solución mínima en la capa correcta.
4. Ejecutar de nuevo el test estrecho.
5. Ejecutar los tests del módulo/app afectada.
6. **Refactor:** aplicar `semantic-compression` y luego `continuous-granularity`.
7. Repetir tests estrechos y ampliar a la suite completa según el riesgo.

Para un bug, el primer test debe reproducir la regresión antes del fix.

## Elegir el tipo de test

- `SimpleTestCase`: lógica pura sin base de datos.
- `TestCase`: modelos, forms, servicios, views y la mayoría de integraciones.
- `TransactionTestCase`: commits reales, `on_commit`, locks, deadlocks o concurrencia.
- `Client`: contratos HTTP, autenticación, mensajes, redirects y templates.

Usar PostgreSQL, no SQLite: los locks, constraints y tipos de este proyecto dependen del motor real.

## Comandos rápidos

```bash
uv run python manage.py test trading.tests.TradingEngineTestCase.test_market_buy_deducts_balance_and_creates_portfolio -v 2
uv run python manage.py test trading.tests -v 2
uv run python manage.py test trading creature banking account dashboard
uv run python manage.py check
uv run python manage.py makemigrations --check --dry-run
```

No ejecutar `seed_all --clear` como parte de tests. Django crea y destruye una base de test aislada.

## Dinero y trading

En cada conducta financiera probar:

- saldo antes/después en `balance_cents`;
- precio, cantidad, comisión y redondeo con `Decimal` construido desde strings;
- atomicidad ante error;
- estado de `Order`, `Trade` y `Portfolio`;
- límites: cero, mínimo fraccional, fondos/holdings insuficientes y circuit breaker.

No afirmar solo la respuesta HTTP: refrescar y comprobar los registros persistidos.

## PostgreSQL y concurrencia

Para flujos con `select_for_update`:

- usar `TransactionTestCase`;
- abrir conexiones/transacciones independientes por worker;
- coordinar threads con barreras o eventos, nunca con sleeps como aserción;
- comprobar la invariante final, no un orden de scheduling accidental;
- cerrar conexiones creadas por threads.

Los tests unitarios normales pueden confirmar resultados; el test concurrente protege contra doble gasto o doble ejecución.

## Celery y Redis

- Probar la lógica en el servicio de forma sincrónica.
- Probar la task como adaptador fino.
- Parchear donde se usa, por ejemplo `creature.tasks.hatch_egg.apply_async`.
- Simular ID de task y errores de conexión cuando corresponda.
- No requerir un worker/beat real para la suite habitual.
- Probar idempotencia llamando dos veces al servicio/task y verificando un único efecto.

Solo usar Redis y procesos reales en una prueba operativa explícita, fuera del ciclo unitario.

## Bordes externos y mocks

Mockear únicamente límites no deterministas o externos: `ExternalBankService`, scheduling Celery, red, reloj y aleatoriedad. No mockear el ORM ni el servicio que se está probando.

Fijar `timezone.now` y `random` cuando el resultado sea parte del contrato. Verificar errores tanto en el dominio como en la view que los traduce a mensajes/respuestas.

Para uploads o seeds, sobrescribir `MEDIA_ROOT` con un directorio temporal y mockear descargas. El test no puede dejar sprites u otros artefactos en el workspace.

## Optimización de la suite

- Preferir `setUpTestData()` para datos inmutables compartidos.
- Crear solo los registros necesarios.
- Reutilizar builders después de observar duplicación real.
- Usar `assertNumQueries` para regresiones N+1 importantes.
- Mantener unitarios e integración enfocados; no duplicar todas las aserciones en ambas capas.

## Brechas actuales a priorizar

Cuando un cambio roce estas áreas, añadir primero cobertura de caracterización:

- ejecución de `check_limit_orders` y tasks periódicas de Celery;
- carreras sobre saldos, órdenes y portfolios;
- login/registro y backend email-or-username;
- dashboard, views bancarias e income/expenses;
- `portfolio_summary_api` y contratos JSON;
- ejecución completa del reward de entrenamiento.

## Criterio de salida

El test falló antes del cambio, queda verde después, las apps afectadas pasan, no hay migraciones olvidadas y los servicios asíncronos se verifican sin depender de infraestructura viva.
