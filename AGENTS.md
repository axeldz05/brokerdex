# Brokerdex: AI Agent Context & Development Rules

Este documento define las reglas de arquitectura, convenciones de código y restricciones de dominio para Brokerdex. El agente de IA debe alinearse estrictamente a estas directrices en cada interacción.

---

## 1. Filosofía del Dominio (Mapeo Financiero)
Brokerdex no es un juego de rol, es una plataforma de trading gamificada.
- **Creatura/Pokémon:** Es el activo financiero subyacente.
- **Órdenes (Order Book):** Sistema P2P de emparejamiento. Un usuario no le compra "al juego", le compra a otro usuario (salvo en mercados sin liquidez regulados por un Market Maker interno).
- **Acciones/Shares:** Se permite la propiedad fraccional de un Pokémon (ej. un usuario puede poseer 0.25 de un Mewtwo).

---

## 2. Stack Tecnológico & Infraestructura local
- **Backend:** Django 5.x con Django REST Framework (DRF).
- **Base de Datos:** PostgreSQL (`brokerdex_db`).
- **Asincronía:** Celery con Redis como Message Broker (`redis://localhost:6379/0`).
- **Cache:** Redis para el estado en tiempo real del Order Book.

---

## 3. Reglas Críticas de Desarrollo (Innegociables)

### Precisión Financiera (Anti-Float)
- **PROHIBIDO** usar `FloatField` o `float` para precios, balances, IVs influyentes o cantidades de activos.
- **OBLIGATORIO:** Usar `DecimalField` en Django y el módulo `decimal` en Python para evitar errores de redondeo de punto flotante.
- Configuración estándar para dinero/precios: `max_digits=12, decimal_places=2`.
- Configuración estándar para fracciones de activos: `max_digits=10, decimal_places=4`.

### Concurrencia y Consistencia (Race Conditions)
- El procesamiento del `OrderBook` y los cambios de balance de billetera (`Wallet`) deben usar transacciones atómicas (`transaction.atomic()`).
- Al modificar balances o ejecutar órdenes emparejadas, usa bloqueo de filas mediante `.select_for_update()` para evitar que dos transacciones simultáneas dupliquen o pisen el dinero/activo.

### Idempotencia en Tareas Asíncronas (Celery)
- Las tareas del *Battle Engine*, *Incubation* y *Training* en Celery deben ser **idempotentes**. Si una tarea se ejecuta dos veces por un fallo de red, el resultado final en la base de datos debe ser el mismo (evitar duplicar recompensas o aplicar dos veces una racha de victorias).
- Registra un identificador único de tarea (`task_id` o un hash del evento de batalla) en la base de datos para validar si ya fue procesado.

---

## 4. Convenciones de Arquitectura de Código

### Estructura de Aplicaciones (Apps de Django)
El proyecto se divide modularmente. No mezcles lógica en un solo lugar:
1. `users_core`: Autenticación, perfiles y billeteras (`Wallet`, `Transaction`).
2. `marketplace`: `Order`, `Trade`, `PriceHistory`, y lógica del motor de emparejamiento.
3. `creatures`: Modelos de Pokémon, estadísticas base, IVs, y evolución.
4. `simulator`: Tareas de Celery para batallas, incubación y entrenamiento.

### Separación de Capas (Fat Models, Thin Views, Rich Services)
- **Views/ViewSets:** Solo manejan validación de entrada (Serializers), estatus HTTP y serialización de salida. No calculan precios ni ejecutan trades.
- **Services (`services.py`):** Toda la lógica de negocio pesada (ej. `match_orders()`, `process_battle_result()`) vive en funciones puras o clases de servicio dentro de cada app.
- **Tasks (`tasks.py`):** Las tareas de Celery solo llaman a las clases de servicio.

---

## 5. Convenciones de API y Gráficos
- Todos los endpoints de series temporales para gráficos (filtros de 1H, 24H, 1W) deben devolver datos estructurados listos para ser consumidos por librerías como Chart.js o ApexCharts: `[{time: "ISO_TIMESTAMP", price: "250.50"}, ...]`.
- Las alertas de volatilidad superiores al 10% deben disparar una señal que guarde una notificación en una tabla `Notification` para consumo por sondeo (polling) o WebSockets en el futuro.
