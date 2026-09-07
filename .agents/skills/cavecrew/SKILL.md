---
name: cavecrew
description: >-
  Guía de decisión y ejecución para delegar tareas a subagentes con salidas ultracomprimidas al estilo caveman. Úsalo cuando se requiera investigar código, realizar ediciones acotadas o revisar diffs mediante subagentes ahorrando ventana de contexto principal. Disparadores: delegar a subagente, usar cavecrew, subagente investigador/builder/reviewer, optimizar contexto.
---

Cavecrew define tres patrones de delegación a subagentes que emiten salidas comprimidas al estilo caveman. La ventaja clave es que el resultado inyectado de vuelta al contexto principal es ~60-70% más pequeño, preservando la ventana de contexto durante sesiones largas.

## Cuándo usar cavecrew vs alternativas

| Tarea | Rol Cavecrew | Alternativa |
|---|---|---|
| "Dónde se define X / qué llama a Y / listar usos de Z" | `cavecrew-investigator` | Búsqueda directa en hilo principal con `grep_search` |
| Búsqueda amplia con sugerencias o arquitectura | Subagente exploratorio estándar | Hilo principal |
| Edición quirúrgica, ≤2 archivos, alcance claro | `cavecrew-builder` | Edición directa con `replace_file_content` |
| Feature nueva / 3+ archivos / refactor transversal | Hilo principal con plan formal | Hilo principal |
| Revisar diff o archivo en busca de bugs | `cavecrew-reviewer` | Auditoría manual en hilo principal |
| Respuesta de una sola línea ya conocida | Hilo principal, sin subagente | Hilo principal |

Regla de oro: **si deseas que el resultado del subagente consuma 1/3 de tokens, instruye al subagente en modo cavecrew. Si requieres prosa extensa, usa modo estándar.**

## Por qué existe (ahorro real de contexto)

Los resultados de subagentes se inyectan en el contexto del agente principal. Un reporte descriptivo que devuelve 2.000 tokens consume 2.000 tokens de la cuota principal. El mismo hallazgo retornado por `cavecrew-investigator` consume ~600-700 tokens. A lo largo de múltiples delegaciones en una sesión, esto evita el agotamiento de contexto y mantiene la precisión del modelo.

## Output contracts

What main thread can rely on per agent:

**`cavecrew-investigator`**
```
<Header>:
- path:line — `symbol` — short note
totals: <counts>.
```
Or `No match.` Always file-path-first, line-number-attached, backticked symbols. Safe to grep with `path:\d+`.

**`cavecrew-builder`**
```
<path:line-range> — <change ≤10 words>.
verified: <re-read OK | mismatch @ path:line>.
```
Or one of: `too-big.` / `needs-confirm.` / `ambiguous.` / `regressed.` (terminal first token).

**`cavecrew-reviewer`**
```
path:line: <emoji> <severity>: <problem>. <fix>.
totals: N🔴 N🟡 N🔵 N❓
```
Or `No issues.` Findings sorted file → line ascending.

## Chaining patterns

**Locate → fix → verify** (most common):
1. `cavecrew-investigator` returns site list.
2. Main thread picks 1-2 sites, hands paths to `cavecrew-builder`.
3. `cavecrew-reviewer` audits the diff.

**Parallel scout** (when investigation is broad):
Spawn 2-3 `cavecrew-investigator` calls in one message (different angles: defs vs callers vs tests). Aggregate in main thread.

**Single-shot edit** (when site is already known):
Skip investigator. Hand exact path:line to `cavecrew-builder` directly.

## What NOT to do

- Don't use `cavecrew-builder` when you don't already know the file. Spawn investigator first or main thread will eat tokens passing context.
- Don't chain `cavecrew-investigator → cavecrew-builder` for a 5-file refactor. Builder will return `too-big.` and you'll have wasted a turn.
- Don't ask `cavecrew-reviewer` for "general feedback" — it returns findings only, no architecture opinions. Use `Code Reviewer` for that.
- Don't expect prose. Cavecrew output is structured, sometimes terse to the point of cryptic. If a human will read it directly, paraphrase.

## Auto-clarity (inherited)

Subagents drop caveman → normal English for security warnings, irreversible-action confirmations, and any output where fragment ambiguity could be misread. Resume caveman after.
