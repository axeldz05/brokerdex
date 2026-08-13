# Brokerdex: reglas de ejecución para Cursor

Este archivo define cómo investigar, implementar, verificar y operar el proyecto. La arquitectura y las invariantes completas están en `AGENTS.md`.

## Secuencia obligatoria de trabajo

1. Antes de leer archivos completos, buscar símbolos y patrones con `rg`/glob.
2. Revisar `git status` y no sobrescribir cambios ajenos.
3. Elegir la capa responsable: form/view para HTTP, servicio para negocio, modelo/manager para invariantes persistentes y task para delegación asíncrona.
4. Aplicar `.cursor/skills/django-tdd/SKILL.md`: test rojo, cambio mínimo y test verde.
5. Al terminar cualquier implementación:
   1. volver a validar con `django-tdd`;
   2. aplicar `.cursor/skills/semantic-compression/SKILL.md`;
   3. aplicar `.cursor/skills/continuous-granularity/SKILL.md`;
   4. repetir tests estrechos, tests de apps afectadas, `uv run flake8`, `uv run python manage.py check` y control de migraciones.
6. No crear commits salvo pedido explícito del usuario.

No se considera terminada una implementación hasta completar el paso 5.

## Gestión del entorno

El entorno canónico usa Python 3.13, `uv`, Django, PostgreSQL, Redis y Celery.

Todo comando, módulo o herramienta Python debe ejecutarse mediante `uv run`; no invocar directamente `python`, `pytest`, `flake8`, `celery` ni otros entrypoints Python. Ejemplos: `uv run python manage.py ...`, `uv run flake8` y `uv run celery ...`.

### Instalación y actualización

```bash
uv sync
uv run python manage.py migrate
```

- `pyproject.toml` y `uv.lock` son la fuente de dependencias; usar `uv add`/`uv remove`, no instalar paquetes sueltos con `pip`.
- Después de cambiar modelos: crear una migración nueva, inspeccionarla y probar ida/vuelta cuando el riesgo lo justifique.
- No editar migraciones ya compartidas ni regenerar datos de producción.

### Servicios locales

Verificar PostgreSQL y Redis antes de arrancar procesos dependientes:

```bash
pg_isready -h localhost -p 5432
redis-cli -u redis://localhost:6379/0 ping
```

Arranque integrado:

```bash
uv run honcho start
```

`Procfile` define:

- `web`: servidor Django;
- `worker`: consumidor Celery;
- `beat`: scheduler periódico.

Mantener una sola instancia de `beat`. Antes de arrancar `honcho`, worker, beat o servidor, comprobar que no haya otra instancia activa. Para trabajo que no toca asincronía puede iniciarse solo el proceso necesario; para validar incubación, entrenamiento, batallas u órdenes límite se requieren Redis, worker y, si se prueban periódicas, beat.

`run.sh` no reemplaza al `Procfile`: solo inicia Redis y Django, sin worker ni beat.

PostgreSQL es obligatorio para desarrollo y tests de integración. No sustituirlo por SQLite: cambiaría el comportamiento de constraints, transacciones y `select_for_update`.

### Datos

```bash
uv run python manage.py seed_all
uv run python manage.py seed_all --demo
```

`seed_all --clear --demo` es destructivo y solo se ejecuta con confirmación explícita en una base local descartable. El seed puede descargar imágenes desde PokeAPI; no usarlo como setup de test ni repetirlo innecesariamente.

## Verificación proporcional al cambio

```bash
# Test individual durante TDD
uv run python manage.py test app.tests.Clase.test_caso -v 2

# Apps afectadas
uv run python manage.py test trading creature banking account dashboard

# Linter
uv run flake8

# Integridad Django y esquema
uv run python manage.py check
uv run python manage.py makemigrations --check --dry-run
```

- Tests normales: broker y servicios externos mockeados.
- Tests de concurrencia: PostgreSQL real y `TransactionTestCase`.
- Cambios financieros: comprobar centavos, `Decimal`, comisión, rollback, locks y registros persistidos.
- Cambios Celery: comprobar servicio síncrono, adaptador task, retries e idempotencia.
- Cambios HTTP/templates: comprobar auth, status/redirect, mensajes, contexto y contrato JSON.
- Ejecutar la suite completa cuando se cambien modelos compartidos, motores de trading/precio, batallas, configuración o migraciones.
- Como no existe CI, estas verificaciones locales son el gate efectivo del repositorio y sus fallos deben informarse explícitamente.

## Operación segura

- Nunca ejecutar dos schedulers Celery Beat sobre la misma cola.
- No ejecutar seeds destructivos, flush, borrado de DB o rollback de migraciones sin autorización.
- No esconder fallos de Redis: distinguir error de scheduling de rollback de negocio.
- No mantener una transacción de PostgreSQL abierta mientras se espera red externa si el flujo puede separarse con seguridad.
- No usar datos demo como requisito implícito de código o tests.
- No leer, imprimir ni copiar el contenido de `brokerdex/.my_pgpass`; es material sensible trackeado y cualquier saneamiento requiere rotación de la credencial.
- Tratar `settings.py` actual como configuración local: no copiar su `SECRET_KEY`, `DEBUG=True` ni credenciales hardcodeadas a despliegues.
- El `Procfile` actual usa `runserver`; es orquestación de desarrollo, no un servidor web de producción.

## Límites de refactor

- Conservar nombres de rutas, contratos JSON y contexto de templates salvo que el cambio los incluya explícitamente.
- Conservar las unidades persistidas: saldo en centavos, precios a 2 decimales, cantidades a 8.
- Conservar atomicidad, orden de locks e historial de `Trade`/`Transfer`.
- No mover lógica de negocio a views, templates, JavaScript o tasks.
- No exponer ELO.
- No convertir el ORM en un repositorio genérico ni ocultar `QuerySet` y primitivas transaccionales.
- No “arreglar de paso” deuda no cubierta por el pedido; documentarla o protegerla con un test si bloquea el cambio.
