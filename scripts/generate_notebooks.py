import json
import os

NOTEBOOKS_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'notebooks')
os.makedirs(NOTEBOOKS_DIR, exist_ok=True)


def create_notebook(filename, cells):
    nb = {
        "cells": cells,
        "metadata": {
            "kernelspec": {
                "display_name": "Python 3",
                "language": "python",
                "name": "python3"
            },
            "language_info": {
                "name": "python",
                "version": "3.13"
            }
        },
        "nbformat": 4,
        "nbformat_minor": 5
    }
    path = os.path.join(NOTEBOOKS_DIR, filename)
    with open(path, 'w', encoding='utf-8') as f:
        json.dump(nb, f, indent=2)
    print(f"Created notebook: {path}")


def md_cell(text):
    return {
        "cell_type": "markdown",
        "metadata": {},
        "source": [line + "\n" for line in text.strip().split("\n")]
    }


def code_cell(code):
    return {
        "cell_type": "code",
        "execution_count": None,
        "metadata": {},
        "outputs": [],
        "source": [line + "\n" for line in code.strip().split("\n")]
    }


# =====================================================================
# Notebook 1: Market & Prices
# =====================================================================
cells_1 = [
    md_cell("""# 📈 01: Análisis de Mercado, Precios y Liquidez
Este notebook analiza la microestructura de mercado de Brokerdex mediante **consultas SQL puras**.

**Preguntas clave a responder:**
1. ¿Cuáles son los activos más caros y cuáles tienen mayor desviación respecto a su precio de referencia?
2. ¿Cómo se comportan las velas japonesas (OHLCV) y el volumen en diferentes intervalos (1H / 1D)?
3. ¿Qué criaturas concentran el mayor volumen negociado en dólares?
"""),
    code_cell("""import pandas as pd
import plotly.graph_objects as go
from plotly.subplots import make_subplots
import plotly.express as px

# Conexión pura a PostgreSQL
from db import get_engine
engine = get_engine()"""),
    md_cell("""## 1. Catálogo de Criaturas y Dispersión de Precios
Consultamos el estado actual de todas las criaturas registradas."""),
    code_cell("""query_catalog = \"\"\"
SELECT 
    name,
    type,
    current_price,
    pricing_reference_price,
    ROUND(((current_price - pricing_reference_price) / NULLIF(pricing_reference_price, 0)) * 100, 2) AS premium_pct,
    wins,
    losses
FROM creature_creature
ORDER BY current_price DESC;
\"\"\"

df_catalog = pd.read_sql_query(query_catalog, engine)
df_catalog"""),
    md_cell("""## 2. Gráfico Interactivo de Velas (Candlesticks) con Volumen
Seleccionamos una criatura para visualizar su historial de velas horarias (1H) con subgráfico de volumen."""),
    code_cell("""target_creature = 'Charizard'

query_ohlcv = f\"\"\"
SELECT 
    timestamp,
    open_price,
    high_price,
    low_price,
    close_price,
    volume
FROM trading_pricehistory ph
JOIN creature_creature c ON ph.creature_id = c.id
WHERE c.name = '{target_creature}' AND ph.interval = '1H'
ORDER BY timestamp ASC;
\"\"\"

df_candles = pd.read_sql_query(query_ohlcv, engine)
df_candles['timestamp'] = pd.to_datetime(df_candles['timestamp'])

# Crear gráfico interactivo con subgráficos de velas y volumen
fig = make_subplots(
    rows=2, cols=1, shared_xaxes=True,
    vertical_spacing=0.05, row_heights=[0.75, 0.25],
    subplot_titles=(f"Velas OHLCV 1H - {target_creature}", "Volumen de Unidades Negociadas")
)

# Velas japonesas
fig.add_trace(
    go.Candlestick(
        x=df_candles['timestamp'],
        open=df_candles['open_price'],
        high=df_candles['high_price'],
        low=df_candles['low_price'],
        close=df_candles['close_price'],
        name="OHLC"
    ),
    row=1, col=1
)

# Volumen
fig.add_trace(
    go.Bar(
        x=df_candles['timestamp'],
        y=df_candles['volume'],
        name="Volumen",
        marker_color="rgba(100, 150, 250, 0.6)"
    ),
    row=2, col=1
)

fig.update_layout(xaxis_rangeslider_visible=False, height=650, template="plotly_dark")
fig.show()"""),
    md_cell("""## 3. Top de Criaturas por Volumen Negociado en Dólares ($)
Consultamos los trades ejecutados agrupados por criatura."""),
    code_cell("""query_volume = \"\"\"
SELECT 
    c.name,
    c.type,
    COUNT(t.id) AS total_trades,
    ROUND(SUM(t.quantity), 4) AS units_traded,
    ROUND(SUM(t.quantity * t.price_per_unit), 2) AS total_volume_usd,
    ROUND(AVG(t.price_per_unit), 2) AS avg_trade_price
FROM trading_trade t
JOIN creature_creature c ON t.creature_id = c.id
GROUP BY c.id, c.name, c.type
ORDER BY total_volume_usd DESC;
\"\"\"

df_volume = pd.read_sql_query(query_volume, engine)

fig_vol = px.bar(
    df_volume,
    x='total_volume_usd',
    y='name',
    orientation='h',
    color='type',
    text='total_volume_usd',
    title="Ranking de Volumen Total Negociado (USD)",
    labels={'total_volume_usd': 'Volumen Total ($)', 'name': 'Criatura'},
    template="plotly_dark"
)
fig_vol.update_layout(yaxis={'categoryorder': 'total ascending'}, height=600)
fig_vol.show()""")
]

# =====================================================================
# Notebook 2: Battles & Metagame
# =====================================================================
cells_2 = [
    md_cell("""# ⚔️ 02: Batallas, ELO y Análisis del Metajuego
Este notebook analiza el comportamiento de los combates y el impacto de los enfrentamientos elementales en la economía de Brokerdex.

**Preguntas clave a responder:**
1. ¿Qué tipos elementales dominan el metajuego en tasa de victorias (*win-rate*)?
2. ¿Existe correlación directa entre el éxito en combate y la cotización de mercado?
3. ¿Cómo se distribuyen los emparejamientos en la matriz de combate?
"""),
    code_cell("""import pandas as pd
import matplotlib.pyplot as plt
import seaborn as sns
import plotly.express as px

from db import get_engine
engine = get_engine()"""),
    md_cell("""## 1. Win-Rate por Criatura y Correlación con el Precio Spot
Consultamos victorias, derrotas y precios actuales."""),
    code_cell("""query_winrates = \"\"\"
SELECT 
    name,
    type,
    wins,
    losses,
    (wins + losses) AS total_battles,
    ROUND(100.0 * wins / NULLIF(wins + losses, 0), 1) AS win_rate_pct,
    current_price,
    pricing_reference_price
FROM creature_creature
WHERE (wins + losses) > 0
ORDER BY win_rate_pct DESC;
\"\"\"

df_wr = pd.read_sql_query(query_winrates, engine)
df_wr"""),
    md_cell("""### Dispersión: Win-Rate vs Precio Spot de Mercado"""),
    code_cell("""fig_scatter = px.scatter(
    df_wr,
    x='win_rate_pct',
    y='current_price',
    size='total_battles',
    color='type',
    hover_name='name',
    text='name',
    title="Relación entre Win-Rate (%) y Precio Spot ($)",
    labels={'win_rate_pct': 'Tasa de Victorias (%)', 'current_price': 'Precio Actual ($)'},
    template="plotly_dark"
)
fig_scatter.update_traces(textposition='top center')
fig_scatter.update_layout(height=600)
fig_scatter.show()"""),
    md_cell("""## 2. Matriz de Enfrentamientos por Tipo Elemental (Matchup Heatmap)
Analizamos qué tipo elemental supera a cuál con un mapa de calor."""),
    code_cell("""query_matchups = \"\"\"
SELECT 
    c1.type AS type_attacker,
    c2.type AS type_defender,
    COUNT(*) AS total_matches,
    SUM(CASE WHEN b.winner_id = bp1.id THEN 1 ELSE 0 END) AS attacker_wins
FROM creature_battle b
JOIN creature_battleparticipant bp1 ON b.id = bp1.battle_id
JOIN creature_battleparticipant bp2 ON b.id = bp2.battle_id AND bp1.id != bp2.id
JOIN creature_creature c1 ON bp1.creature_id = c1.id
JOIN creature_creature c2 ON bp2.creature_id = c2.id
WHERE b.status = 'finished' AND b.winner_id IS NOT NULL
GROUP BY c1.type, c2.type;
\"\"\"

df_matchups = pd.read_sql_query(query_matchups, engine)

if not df_matchups.empty:
    df_matchups['win_rate'] = (df_matchups['attacker_wins'] / df_matchups['total_matches']) * 100
    pivot_table = df_matchups.pivot(index='type_attacker', columns='type_defender', values='win_rate').fillna(50)

    plt.figure(figsize=(10, 8))
    sns.heatmap(pivot_table, annot=True, fmt=".1f", cmap="vlag", center=50, cbar_kws={'label': 'Win Rate (%)'})
    plt.title("Matriz de Enfrentamientos: Win-Rate (%) Tipo Atacante vs Defensor", fontsize=14)
    plt.xlabel("Tipo Defensor")
    plt.ylabel("Tipo Atacante")
    plt.tight_layout()
    plt.show()
else:
    print("Aún no hay suficientes batallas finalizadas registradas para generar la matriz completa.")""")
]

# =====================================================================
# Notebook 3: Bots & Macroeconomics
# =====================================================================
cells_3 = [
    md_cell("""# 🤖 03: Bots Autónomos, Riqueza y Macroeconomía
Este notebook analiza la salud macroeconómica de Brokerdex, la velocidad del dinero y la distribución de riqueza entre los agentes.

**Preguntas clave a responder:**
1. ¿Cuál es la masa monetaria total ($M_1$) en circulación y en reservas?
2. ¿Cómo de equitativa o concentrada está la riqueza entre los 100 entrenadores (Curva de Lorenz y Coeficiente de Gini)?
3. ¿Cuánto dinero en comisiones recauda la plataforma?
"""),
    code_cell("""import pandas as pd
import numpy as np
import plotly.express as px
import plotly.graph_objects as go

from db import get_engine
engine = get_engine()"""),
    md_cell("""## 1. Masa Monetaria del Sistema ($M_1$)
Calculamos la liquidez en saldos de usuarios excluyendo la cuenta interna del Market Maker."""),
    code_cell("""query_m1 = \"\"\"
SELECT 
    COUNT(*) AS total_entrenadores,
    ROUND(SUM(balance_cents) / 100.0, 2) AS dinero_liquido_total_usd,
    ROUND(AVG(balance_cents) / 100.0, 2) AS saldo_promedio_usd,
    ROUND(MIN(balance_cents) / 100.0, 2) AS saldo_minimo_usd,
    ROUND(MAX(balance_cents) / 100.0, 2) AS saldo_maximo_usd
FROM account_account
WHERE is_active = true AND username != 'market_maker';
\"\"\"

df_m1 = pd.read_sql_query(query_m1, engine)
df_m1"""),
    md_cell("""## 2. Distribución de Riqueza y Coeficiente de Gini
Calculamos el patrimonio neto total de cada usuario (efectivo líquido + valor de mercado de sus criaturas)."""),
    code_cell("""query_wealth = \"\"\"
SELECT 
    a.username,
    ROUND(a.balance_cents / 100.0, 2) AS cash_usd,
    ROUND(COALESCE(portfolio_val.total_asset_value, 0), 2) AS asset_value_usd,
    ROUND((a.balance_cents / 100.0 + COALESCE(portfolio_val.total_asset_value, 0)), 2) AS net_worth_usd
FROM account_account a
LEFT JOIN (
    SELECT 
        p.owner_id,
        SUM(p.quantity * c.current_price) AS total_asset_value
    FROM trading_portfolio p
    JOIN creature_creature c ON p.creature_id = c.id
    GROUP BY p.owner_id
) portfolio_val ON a.id = portfolio_val.owner_id
WHERE a.is_active = true AND a.username != 'market_maker'
ORDER BY net_worth_usd ASC;
\"\"\"

df_wealth = pd.read_sql_query(query_wealth, engine)

# Cálculo de la Curva de Lorenz y Coeficiente de Gini
wealth_values = np.sort(df_wealth['net_worth_usd'].values)
n = len(wealth_values)
cum_wealth = np.cumsum(wealth_values)
lorenz_curve = cum_wealth / cum_wealth[-1]
equality_line = np.linspace(0, 1, n)

# Coeficiente de Gini
gini = (np.sum((2 * np.arange(1, n + 1) - n - 1) * wealth_values)) / (n * np.sum(wealth_values))

# Gráfico de Lorenz
fig_lorenz = go.Figure()
fig_lorenz.add_trace(go.Scatter(x=equality_line, y=equality_line, mode='lines', name='Igualdad Perfecta (Gini = 0)', line=dict(dash='dash', color='gray')))
fig_lorenz.add_trace(go.Scatter(x=equality_line, y=lorenz_curve, mode='lines', name=f'Curva de Lorenz Real (Gini = {gini:.3f})', line=dict(color='#00CC96', width=3)))

fig_lorenz.update_layout(
    title=f"Curva de Lorenz y Desigualdad de Riqueza (Coeficiente de Gini: {gini:.3f})",
    xaxis_title="Proporción Acumulada de Entrenadores",
    yaxis_title="Proporción Acumulada de Riqueza Total",
    template="plotly_dark",
    height=550
)
fig_lorenz.show()"""),
    md_cell("""## 3. Recaudación de Comisiones de la Plataforma
Total de ingresos generados por la comisión del 1.5% en cada fill ejecutado en el libro de órdenes."""),
    code_cell("""query_fees = \"\"\"
SELECT 
    COUNT(*) AS total_trades,
    ROUND(SUM(commission), 2) AS comisiones_totales_usd,
    ROUND(AVG(commission), 2) AS comision_promedio_por_trade_usd,
    ROUND(SUM(quantity * price_per_unit), 2) AS volumen_total_usd
FROM trading_trade;
\"\"\"

df_fees = pd.read_sql_query(query_fees, engine)
df_fees""")
]


if __name__ == '__main__':
    create_notebook('01_market_and_prices.ipynb', cells_1)
    create_notebook('02_battles_and_metagame.ipynb', cells_2)
    create_notebook('03_bots_and_macroeconomics.ipynb', cells_3)
