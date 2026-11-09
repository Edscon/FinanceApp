# Plan: sistema de detección de gaps entre valores correlacionados

Sistema de trading algorítmico que detecta *gaps* (desviaciones anómalas) entre títulos que
normalmente se mueven juntos. Este documento recoge el plan, las decisiones de diseño y el estado de
cada fase. La investigación de estrategias y el veredicto con datos reales están en
[ESTRATEGIAS.md](ESTRATEGIAS.md).

> No es asesoramiento financiero. Ningún backtest garantiza resultados y operar puede causar
> pérdidas.

## 1. Idea y principio rector

- **Gap** = desviación anómala de un valor frente a los que se mueven con él (su grupo o su par),
  con la apuesta de que se cierra.
- **No se construye ejecución de órdenes hasta que un backtest realista demuestre ventaja.** Ahora
  mismo la app es solo de backtesting, sin broker.

## 2. Estado

| Fase | Contenido | Estado |
|---|---|---|
| 0. Higiene | Repo limpio, configuración por entorno | ✔ Hecho. **Falta rotar la `FMP_API_KEY`** (estuvo versionada) |
| 1. Datos e investigación | Precios ajustados (Yahoo, FMP, CSV), universos, escáner de pares, calibración del test | ✔ Hecho |
| 2. Backtest realista | Motor con ejecución en t+1, costes, walk-forward, robustez, puerta y web | ✔ Hecho. **Puerta no superada** con ninguna estrategia (ver ESTRATEGIAS.md) |
| 3. Web de señales en vivo | Escáner diario con alertas | ⏸ En pausa: no tiene sentido sin una estrategia que supere la puerta |
| 4. Paper trading y ejecución | Broker, órdenes, gestión de riesgo | ⏸ En pausa (misma razón) |

**Decisión según la puerta:** las estrategias probadas no justifican pasar a la Fase 3. Los
siguientes pasos razonables son de investigación, y cada uno debe declararse antes de ejecutarlo
(sección 6 de ESTRATEGIAS.md).

## 3. Arquitectura

```
engine/                     Python puro, sin Django (mismo código en notebooks, backtests y web)
  data/providers.py         Yahoo, FMP, CSV -> barras ajustadas
  data/synthetic.py         mercados sintéticos con estructura conocida (tests y demo)
  research/                 estadística de pares, find_pairs, informe
  backtest/                 config, MarketData, MarketView, simulador, métricas, robustez
  backtest/strategies/      reversión intra-sector, pares walk-forward
  calibration.py            potencia y tamaño del test de cointegración
core/                       cola de trabajos en BD (hilo en el servidor o run_worker), layout, panel
market/                     Instrument, DailyBar, Universe; importación; presets; demo
backtests/                  BacktestRun, Trade, SelectionCache; páginas; escáner y página de par
```

- **Stack:** Django 5.2 + HTMX 2 + Tailwind v4 + TradingView Lightweight Charts 5, todo servido
  localmente.
- **Base de datos:** SQLite en modo WAL, suficiente para uso local (300 valores × 5 años se cargan
  en ~25 s).

## 4. Decisiones de diseño del motor

- **Una sola regla temporal.** La estrategia decide al cierre de t viendo solo datos hasta t
  (arrays truncados y de solo lectura) y se ejecuta en t+1, por defecto al cierre. Las aperturas
  gratuitas son ruidosas y generan reversión falsa. Un test altera el futuro y comprueba que el
  pasado no cambia.
- **Contabilidad por acciones:**
  - comisión y deslizamiento en pb;
  - préstamo de cortos por días naturales;
  - cierre forzado tras más de 3 sesiones sin precio;
  - liquidación al final.
  La atribución por valor suma exactamente el equity.
- **Pares:**
  - Selección solo con la formación: Engle-Granger en las dos orientaciones, FDR y half-life.
  - α, β y σ del spread quedan fijos en la operativa.
  - La selección (la parte cara) se cachea y las variantes la reutilizan.
- **Escáner:** los parámetros se estiman sin la última sesión, para que el propio gap no contamine
  el test que califica al par.
- **Valores por defecto fijados antes de ver datos** y contador de configuraciones probadas, como
  defensa contra el sobreajuste.

## 5. Lo medido

- **Potencia del test de cointegración** (`python -m engine.calibration`), con 504 sesiones:
  - detecta el 100 % de los pares con half-life ≤ 7 días, el 98 % a 10, el 64 % a 15 y el 24 % a 30;
  - con 756 sesiones, el 96 % a 15 días;
  - falsos positivos: 4–6 %.
- **Resultado con datos reales:** ESTRATEGIAS.md, sección 4.

## 6. Riesgos y límites conocidos

- **Sesgo de supervivencia:** los universos son los de hoy; un resultado positivo sería optimista.
  Ya se vio en vivo con BK (ahora BNY) y con EA, EQR y AVB, que dejaron de cotizar en 2026.
- **Ajustes de Yahoo:** pueden ser erróneos en escisiones (p. ej. DHR en 2016). La app marca los
  movimientos diarios extremos.
- **Costes:** deciden el resultado. El break-even por lado se muestra en cada backtest.
- **Regulación (si algún día se opera):** para uso personal normalmente no aplica. Dar señales a
  terceros o mostrar datos en tiempo real sí puede requerir licencias.

## 7. Próximos pasos posibles

Solo de investigación, y cada uno declarado antes de ejecutarlo:
- Costes por valor según su diferencial real.
- Menos rotación: bandas, cuantiles o solo los gaps extremos.
- Residuos frente a factores.
- Universos con más dispersión.
- Datos sin sesgo de supervivencia.
