---
name: semantic-compression
description: >-
  Refactoriza Brokerdex por compresión semántica después de implementar una conducta: parte de casos concretos cubiertos por tests, extrae únicamente duplicación real y conserva las invariantes financieras, transaccionales y asíncronas de Django. Usar al cerrar toda implementación o refactor.
---

# Compresión semántica para Brokerdex

## Objetivo

Reducir el costo total del código sin anticipar abstracciones. Primero hacer correcta y testeable la conducta; después comprimir conceptos repetidos desde abajo hacia arriba.

## Flujo obligatorio

1. Localizar con `grep_search` o `rg` la conducta nueva y sus equivalentes.
2. Confirmar el comportamiento con un test verde.
3. Marcar repeticiones de intención, no solo similitudes sintácticas.
4. Extraer el nivel mínimo que nombre mejor el dominio.
5. Mantener las transacciones, locks, redondeos y contratos HTTP visibles.
6. Ejecutar el test más estrecho y luego la suite de las apps afectadas.
7. Proceder con [continuous-granularity](file:///home/drusila/Projects/brokerdex-django/.agents/skills/continuous-granularity/SKILL.md).

Si solo existe un caso real, dejarlo concreto. No crear una capa “por si acaso”.

## Escalera de compresión Django

Subir un peldaño por vez:

1. Código explícito en el caso de uso.
2. Función privada local al módulo.
3. Método de servicio cuando coordina una operación de negocio.
4. Método de modelo, propiedad o `QuerySet` cuando expresa una regla intrínseca o una consulta componible.
5. Componente compartido solo cuando varias apps consumen el mismo concepto estable.

Para templates: HTML concreto → `{% include %}` repetido → tag/filtro únicamente si hay lógica de presentación reutilizada. No mover lógica financiera a templates o JavaScript.

## Qué comprimir

- Conversión de dinero repetida, con `Decimal` y cuantización explícita.
- Consultas repetidas con el mismo significado y necesidades de `select_related`/`prefetch_related`.
- Validaciones idénticas en forms y servicios, dejando la regla autoritativa en el dominio.
- Preparación repetida de contexto o serialización en el mismo contrato.
- Fixtures/builders de test solo después de repetirse.

La señal correcta es que cambiar una regla de negocio requiera editar un único punto autoritativo.

## Límites que no deben diluirse

- `balance_cents` sigue siendo entero en centavos.
- Precios y cantidades siguen siendo `Decimal`; `float` solo puede aparecer en el borde de presentación si el contrato existente lo exige.
- Toda mutación de saldo, orden, portfolio, incubación o entrenamiento conserva `transaction.atomic()` y `select_for_update()`.
- `Trade` continúa como registro histórico; no recalcular hechos ejecutados con tasas actuales.
- Tasks Celery permanecen finas y delegan a servicios idempotentes.
- Las migraciones aplicadas no se reescriben; los cambios de esquema generan migraciones nuevas.
- ELO continúa siendo detalle interno y no se expone al usuario.

## Anti-patrones

- Repositorio genérico sobre el ORM de Django.
- `BaseService`, jerarquías profundas o mixins creados para un único uso.
- DTOs paralelos que copian campos de modelos sin cambiar el contrato.
- Signals para ocultar flujos financieros o efectos relevantes.
- Un “utils.py” genérico sin dueño semántico.
- Helper que mezcla validación HTTP, negocio, persistencia y scheduling.
- Abstracción que obliga a perder `QuerySet`, `select_for_update`, `update_fields` o control de errores.

## Criterio de salida

La implementación queda más corta en conceptos, no necesariamente en líneas; los call sites leen como acciones del dominio; sigue siendo posible depurar cada operación; y todos los tests afectados permanecen verdes.
