---
name: continuous-granularity
description: Revisa que las abstracciones de Brokerdex mantengan granularidad continua: las utilidades de alto nivel deben conservar acceso a forms, servicios, QuerySets, transacciones, tasks y primitivas de template. Usar después de toda implementación y compresión semántica.
---

# Granularidad continua para Brokerdex

## Objetivo

Comprimir sin crear “agujeros” de API. Cada wrapper de alto nivel debe ser reemplazable por las piezas Django que compone cuando aparece un caso especial.

## Niveles que deben seguir disponibles

### Backend

1. Primitivas: modelos, `QuerySet`, `Decimal`, `transaction.atomic`, `select_for_update`.
2. Operaciones de dominio: métodos concretos en managers y servicios.
3. Orquestación: views/forms, management commands y tasks Celery.

### Frontend server-rendered

1. HTML, variables de contexto y tags nativos.
2. Includes y template tags reutilizables.
3. Layouts y componentes de página.

### Testing

1. Tests puros de funciones/reglas.
2. Tests de modelo/servicio con PostgreSQL.
3. Tests HTTP, integración Celery y concurrencia.

Una capa nueva no puede eliminar ni volver impráctico el nivel inferior.

## Revisión posterior a cada implementación

1. Enumerar los wrappers, helpers o servicios creados/modificados.
2. Para cada uno, identificar las primitivas que encapsula.
3. Probar mentalmente un caso con un parámetro, lock, filtro o respuesta HTTP diferente.
4. Si el caller no puede bajar un nivel sin duplicar todo el flujo, abrir la API mínima necesaria.
5. Verificar con tests el wrapper y al menos una primitiva relevante.

## Reglas Django

- Los servicios aceptan objetos o valores explícitos; no deben depender silenciosamente de `request`.
- Las views conservan control sobre forms, mensajes, status y redirects.
- Los helpers de ORM devuelven `QuerySet` cuando la composición posterior es útil; no convertir prematuramente a `list`.
- No esconder locks dentro de una abstracción que impida ordenar adquisiciones o ampliar el bloque atómico.
- Los managers pueden ofrecer operaciones frecuentes, pero `objects` y las consultas directas siguen disponibles.
- Una task Celery llama a un servicio; el servicio también debe poder probarse y ejecutarse sin broker.
- Los errores de dominio siguen siendo distinguibles de fallos de Redis, red o HTTP.
- Los includes de template aceptan contexto explícito y no impiden escribir markup específico al lado.
- Los endpoints JSON mantienen tipos y claves del contrato aunque internamente se comprima la construcción.

## Adaptar sin destruir

Ante una variante específica, añadir una capa fina o un parámetro con default compatible. No convertir una operación general en una especial.

Ejemplos:

- Mantener `execute_market_buy()` y construir encima una orquestación HTTP; no pasar `request` al motor de trading.
- Mantener `BattleService.process_battle_result()` invocable directamente aunque Celery la programe.
- Mantener consultas de historial componibles aunque exista una función que produzca el contexto final.
- Mantener la posibilidad de un bloque HTML concreto aunque exista un include para la tarjeta común.

## Señales de discontinuidad

- Para una variación pequeña hay que copiar el helper completo.
- Un wrapper devuelve solo datos renderizados y se pierde el objeto/`QuerySet`.
- Una función “mágica” abre transacciones, agenda tasks y emite mensajes sin que el caller lo controle.
- Un mock exige conocer detalles internos porque no existe un borde explícito.
- Un componente fuerza todas las páginas a aceptar props que solo usa una.

## Criterio de salida

La ruta habitual queda compacta, la ruta excepcional sigue disponible y las capas respetan sus responsabilidades. Ejecutar nuevamente los tests estrechos después de cualquier ajuste.
