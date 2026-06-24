Comenza continuando la implementacion de este proyecto

# 📈 Brokerdex: Pokémon-Themed Investment Platform

**Brokerdex** es una plataforma de simulación financiera y *trading* donde los activos tradicionales (acciones, bonos, commodities) son reemplazados por criaturas (Pokémon). El objetivo principal del proyecto es gamificar la educación financiera y el análisis de mercados, adaptando conceptos macro y microeconómicos complejos a un entorno dinámico, competitivo e intuitivo.

---

## 🗺️ Mapeo de Conceptos: Finanzas vs. Pokémon

Para mantener la coherencia del simulador, el ecosistema de juego se rige bajo la siguiente equivalencia financiera:

| Concepto Financiero Tradicional | Equivalente en Brokerdex | Descripción / Mecánica |
| --- | --- | --- |
| **Acción / Activo (Stock)** | **Creatura (Pokémon)** | El activo subyacente cuyo valor fluctúa en el mercado. |
| **IPO (Oferta Pública Inicial)** | **Incubación (Hatching)** | Introducción de un nuevo activo al mercado con *stats* (IVs) base únicos. |
| **Mejora de Capital (Capex)** | **Entrenamiento (Training)** | Inversión de capital para mejorar los *stats* del activo, aumentando su valor intrínseco. |
| **Split de Acciones / Fusión** | **Evolución (Evolution)** | Transformación del activo que quema la unidad anterior y genera un impacto masivo en su precio base. |
| **Reporte de Ganancias (Earnings)** | **Resultados de Batallas** | Eventos programados (asíncronos) que determinan el rendimiento de la inversión. |
| **Índice de Mercado (ej. S&P500)** | **Índices de Tipo (Type Indices)** | Canastas de activos agrupados por su tipo (ej. *Index Kanto Fire-Type*). |

---

## 🏗️ Arquitectura del Sistema y Backend Asíncrono

El núcleo de Brokerdex depende de una arquitectura desacoplada para manejar la alta concurrencia de transacciones y simulaciones en tiempo real sin bloquear la experiencia del usuario (UI).

```
[ User Dashboard ] <---> [ Django REST / Views ] <---> [ PostgreSQL ]
                                ^
                                | (Triggers / Eventos)
                                v
                         [ Redis Message Broker ]
                                ^
                                |
                        [ Celery Workers ]
                 (Battle Engine, Incubation, Prices)

```

---

## ⚙️ Desglose Detallado de Características (Plan de Ejecución)

### 1. Core & Motor de Precios Dinámicos (Dynamic Pricing Engine)

El precio de mercado (`current_price`) de cada criatura no es estático; se calcula mediante un algoritmo que simula la oferta, la demanda y el rendimiento deportivo.

* **Fórmula Base de Fluctuación:**

$$\text{Precio Actual} = \text{Precio Base} \times (1 + \Delta_{\text{Batallas}} + \Delta_{\text{Mercado}})$$


* $\Delta_{\text{Batallas}}$: Multiplicador basado en rachas de victorias/derrotas de la especie o espécimen en el *Battle Engine*.
* $\Delta_{\text{Mercado}}$: Ratio de liquidez (volumen de órdenes de compra vs. órdenes de venta en el Order Book).


* **(Rarity Multiplier):** Los Pokémon legendarios o con IVs (Individual Values) perfectos poseen una menor tasa de aparición, lo que genera escasez y una prima de precio natural.

### 2. Sistema de Trading (Order Book)

Un sistema de emparejamiento de órdenes *Peer-to-Peer* (P2P) simplificado.

* **Órdenes de Mercado (Market Orders):** Compra/venta inmediata al mejor precio disponible.
* **Órdenes Límite (Limit Orders):** El usuario establece un precio específico. La orden se almacena en caché (Redis) y se ejecuta solo cuando el precio del Pokémon alcanza dicho umbral.
* **Mesa de Dinero Central (Liquidity Pool):** Para Pokémon comunes, la plataforma actúa como "Creador de Mercado" (Market Maker) permitiendo transacciones instantáneas usando una tasa de comisión fija.

### 3. Simulaciones Asíncronas (Celery & Redis Task Queue)

* **Async Battle Engine:** * Cada X minutos, Celery selecciona criaturas del ecosistema para enfrentarse en torneos simulados basados en sus estadísticas reales (Ataque, Defensa, Velocidad, Ventaja de Tipo).
* Al finalizar, una señal de Django (`post_save`) actualiza el historial de precios y distribuye recompensas.


* **Incubation System (Sistema de Incubación):**
* Los usuarios compran "Huevos" con la moneda interna.
* Un *worker* de Celery gestiona un contador de tiempo de fondo. Al expirar, se realiza el *minting* del nuevo Pokémon con un algoritmo de aleatoriedad para sus IVs.


* **Training Mechanism (Capitalización):**
* El usuario bloquea su activo durante un tiempo determinado y paga un fee (quema de tokens). Al terminar el proceso asíncrono, las estadísticas aumentan permanentemente, elevando su valor de reventa.



### 4. Herramientas de Análisis Financiero y Estadísticas

* **Gráficos de Candelas y Líneas:** Integración en el frontend de gráficos interactivos usando **Chart.js** o **ApexCharts** para visualizar el volumen de transacciones y el precio histórico (filtros de 1H, 24H, 1W, 1M).
* **Market Indices (Índices Sectoriales):**
* *Water-Type Index (WTI):* Promedio ponderado del rendimiento de los activos de agua del mercado. Permite ver la tendencia macro del ecosistema.


* **Alertas de Volatilidad (Circuit Breakers):**
* Si un activo sube o baja más del 10% en un periodo de 5 minutos debido a rachas en las batallas, se congela su trading temporalmente (Circuit Breaker) y se envía una notificación push/alerta en el dashboard al usuario.


* **Portfolio Analytics (P&L Dashboard):**
* Métricas en tiempo real: ROI total, Retorno Diario, Diversificación del portafolio (gráfico de dona por tipos de Pokémon) y balance de ganancias/pérdidas realizadas y no realizadas.


## 🗺️ Roadmap de Desarrollo

### Fase 1: Consolidación del Core Financiero (Sprints 1-2)

* [x] Diseñar el modelo de datos para el `OrderBook`, `Trade`, y `PriceHistory`.
* [x] Desarrollar los endpoints de la API para publicar órdenes de compra/venta límite y de mercado.
* [x] Implementar la lógica matemática del **Dynamic Pricing Engine** inicial (fluctuación aleatoria controlada).
    * Tests de integración: cobertura completa con 14 tests end-to-end (HTTP → view → service → DB).

### Fase 2: Automatización y Procesos de Fondo (Sprints 3-4)

* [x] Configurar Celery y Redis en el entorno de desarrollo (Docker recomendado).
    * `brokerdex/celery.py` + `brokerdex/__init__.py` con app Celery.
* [x] Desarrollar el script del **Battle Engine** (lógica de combate rápida basada en stats).
    * `matchmake_random_battle()` / `process_battle_turn()` en `creature/tasks.py`.
* [x] Crear las tareas programadas (Celery Beats) para las batallas automáticas cada 5 minutos.
* [x] Desarrollar los servicios asíncronos de Incubación.
    * `EggTemplate` + `Incubation` models, `IncubationService`, `hatch_egg` Celery task.
    * Tests de integración: 8 tests (shop, purchase, hatching, status).
* [x] Desarrollar los servicios asíncronos de Entrenamiento.
    * `TrainingService`, `complete_training` Celery task (stat boost + price update).
    * Tests de integración: 5 tests (cost, balance, 404, Celery call).

### Fase 3: Analytics, Visualización y UI Premium (Sprints 5-6)

* [x] Crear los comandos de agregación de datos para calcular los índices de mercado diarios (`MarketIndices`).
    * `MarketIndex` model, `MarketIndicesService`, `calculate_market_indices` Celery task.
    * Tests de integración: 6 tests (cálculo, page, API).
* [ ] Conectar el frontend con las librerías de gráficos para renderizar el historial de precios en tiempo real.
    * Chart.js integrated in creature detail template (line chart).
* [x] Desarrollar la sección de analíticas de portafolio del usuario (cálculo automatizado de P&L y ROI).
    * Unrealized P&L, Realized P&L, Total Return, type distribution donut chart.
    * Tests de integración: 14 tests de trading + portafolio.
* [x] Implementar el sistema de alertas visuales de volatilidad.
    * `Notification` model, `VolatilityService`, circuit breaker en `Creature`.
    * Tests de integración: 7 tests (alerta, circuit breaker, notificaciones).

