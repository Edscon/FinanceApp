# Plan: detección de gaps entre valores correlacionados

Sistema de trading algorítmico que detecta *gaps* (desviaciones anómalas) entre títulos que
normalmente se mueven juntos. Este documento recoge el plan, las decisiones de diseño y el
estado de cada fase; se actualiza a medida que avanza el proyecto.

> No es asesoramiento financiero. Ningún backtest garantiza resultados y operar puede causar
> pérdidas.

## 1. Idea y supuestos

- **Gap** = desviación anómala entre dos (o más) títulos que normalmente se mueven juntos, con la
  apuesta de que vuelven a su relación habitual (arbitraje estadístico / *pairs trading*).
- MVP: acciones líquidas de EE. UU., barras diarias, uso personal, largo/corto neutral al mercado.
- **Principio rector:** no se construye la ejecución de órdenes hasta que un backtest realista
  demuestre que hay ventaja. La interfaz es lo fácil; lo difícil es saber si la ventaja existe.

## 2. Tipos de gap

| Tipo | Idea | Datos | Cuándo |
|---|---|---|---|
| **Spread** | `log y − α − β·log x` se aleja más de 2σ de su media: se apuesta a la convergencia | diario, luego 5–15 min | MVP (implementado) |
| **Apertura relativo** | y abre con gap y su par x no: `gap_y − β·gap_x` fuera de lo normal | OHLC diario | Fase 2 (mismo motor de pares) |
| **Lead–lag** | y se mueve y x reacciona con retraso de segundos o minutos | tick / 1 s | Opcional: exige baja latencia y es lo primero que se arbitra |

**Correlación no es cointegración.** Dos acciones con ρ = 0,9 pueden divergir de forma permanente.
La correlación solo *preselecciona* candidatos; para operar, el spread debe ser estacionario.

## 3. Arquitectura

```
engine/                   núcleo cuantitativo en Python puro (sin Django)
  data/providers.py       FMP y CSV -> barras ajustadas (Bar)
  data/synthetic.py       series con estructura conocida, para tests y demos
  research/stats.py       OLS, half-life, Engle-Granger, FDR, z-score
  research/pairs.py       find_pairs(): candidatos -> cointegración -> ranking
  research/report.py      informe de texto
  calibration.py          potencia y tamaño del test, por simulación
pairs/                    app Django: DailyBar, import_prices, find_pairs
screener/                 screener fundamental (anterior al sistema de pares)
```

- `engine/` no depende de Django: el mismo código sirve en notebooks, backtests y en el proceso
  en vivo (menos sorpresas al pasar a real). Django solo persiste datos y muestra resultados.
- Los procesos largos (datos, señales, órdenes) irán como *workers* aparte, no dentro de las
  peticiones web.
- SQLite basta para las Fases 1–2: cargar 300 valores × 5 años (378.000 barras) tardó 26 s
  (fichero de 35 MB) y escanear 3.790 pares candidatos, 19 s. PostgreSQL (y TimescaleDB si se pasa
  a intradía) llega cuando haya *workers* concurrentes.

## 4. Motor de detección (`find_pairs`)

1. **Ventana:** últimas `lookback` sesiones (504 por defecto); se descartan los tickers con huecos.
2. **Candidatos:** pares del mismo grupo (sector/industria) con correlación de retornos ≥ `min_corr`.
3. **Validación:** test de Engle-Granger sobre log-precios ajustados, **en las dos orientaciones**
   (se queda con el mejor p-valor multiplicado por 2), corrección **FDR de Benjamini-Hochberg** por
   el número de pares probados, half-life (AR(1)) dentro de `[min_hl, max_hl]`.
4. **Señal:** z-score del spread. `|z| ≥ entry_z` (2 por defecto) marca el gap; z > 0 significa `y`
   caro frente a `x` (corto `y`, largo `x`). Como β se estima en logaritmos, el ratio de nocionales
   es 1 : β.
5. **Diagnóstico de estabilidad:** ¿sigue cointegrando en cada mitad de la ventana (p ≤ 0,10)?
   Informativo, no filtra.

### Decisiones de diseño y por qué

- **Holdout:** los parámetros (correlación, cointegración, β, half-life, media y σ del spread) se
  estiman **sin la última sesión**; esa sesión solo mide el gap. Si entrara en la estimación, un
  salto grande en la última sesión empeora el propio test que califica al par y puede hacerlo
  desaparecer justo cuando más interesa (se observó en simulación). Es coherente con la
  "recalibración nocturna" prevista: parámetros de ayer, z de hoy.
- **Dos orientaciones + ×2:** Engle-Granger no es simétrico; probar las dos y quedarse con la mejor
  sin corregir inflaría los falsos positivos.
- **FDR sobre los pares probados:** con 100 acciones hay ~4.950 pares; algunos "cointegrarán" por
  puro azar.
- **Precios ajustados** por splits y dividendos: valen para retornos y spreads, **no** para
  dimensionar órdenes en acciones (ahí hará falta el precio real).

### Lo que aún NO hace el escáner

Filtros de earnings/noticias (un gap con noticia tiende a continuar, no a revertir), liquidez,
*halts*, coste esperado frente a beneficio esperado, clustering jerárquico y estimación de β con
filtro de Kalman. Están previstos; hasta entonces los resultados son una lista de candidatos, no
de operaciones.

## 5. Lo que se ha medido

`python -m engine.calibration` simula pares con cointegración real y sin ella. Con **504 sesiones**
(100 pares por fila, ±5 puntos de ruido de muestreo):

| Half-life real | 3–7 d | 10 d | 15 d | 20 d | 30 d |
|---|---|---|---|---|---|
| Pares detectados | 100 % | 98 % | 64 % | 40 % | 24 % |

Falsos positivos (nominal 5 %): 4 % con caminos independientes y 6 % con pares correlados (~0,8)
por un factor común pero sin cointegración.

- Con ~2 años de datos diarios el test **solo ve con fiabilidad reversiones de hasta ~10–15 días**.
  Que un par con half-life largo no aparezca no significa que no cointegre.
- Una ventana de **756 sesiones** sube la detección a 100 % (10 d), 96 % (15 d), 80 % (20 d) y 34 %
  (30 d), a costa de más riesgo de cambios estructurales en el periodo.
- Una ventana de **300 sesiones** empeora todo: detecta 51 % a 10 d y los falsos positivos con
  caminos independientes suben al 7,5 %. No usar ventanas cortas.
- **A escala** (una simulación: 300 valores en 10 sectores muy correlados, con 10 pares cointegrados
  sembrados y 3.790 pares probados): con 504 sesiones aparecen 9 de los 10 y ningún falso positivo;
  con 756 sesiones, los 10. Corregir por tests múltiples cuesta algo de potencia cuando se prueban
  miles de pares; una ventana más larga la recupera.

## 6. Backtesting y puertas de decisión (Fase 2)

- Costes realistas: comisión + spread + slippage + **coste y disponibilidad del corto**; ejecución
  en la barra siguiente, nunca al precio de la señal.
- Sin sesgos: universo con valores deslistados (si no, el resultado sale inflado), precios
  ajustados, sin *look-ahead*.
- Walk-forward (formación → trading) y sensibilidad: costes ×2, parámetros ±20 %.
- **Puerta:** no se construye ejecución hasta que la ventaja sobreviva fuera de muestra y con costes
  ×1,5 (p. ej. Sharpe neto > 1; umbral a fijar al empezar la Fase 2).

## 7. Web (Fase 3)

Dashboard (equity, P&L, posiciones, salud de feeds), scanner (pares ordenados por |z|, filtros,
alertas, heatmap de correlaciones), detalle de par (precios normalizados, spread con bandas ±1/2/3σ,
correlación y p-valor rodantes, β, half-life, marcas de earnings), laboratorio de backtests, operativa
(modo manual o automático, **kill switch**) y riesgo/ajustes (límites, alertas por Telegram o email,
auditoría señal → orden → fill). Con barras diarias basta recalcular cada N minutos con el último
precio; el streaming tick a tick solo hace falta para lead–lag.

## 8. Ejecución y riesgo (Fase 4)

- Modos progresivos: solo alertas → semiautomático → automático (solo tras paper trading).
- Interactive Brokers (cortos, paper, disponible en España) con `ib_async`, detrás de una interfaz
  `BrokerAdapter`; Alpaca si opera en el país del usuario.
- Controles antes de cada orden: riesgo por par ≈ 0,25–0,5 % del capital en el stop, neutral en
  dólares y beta, máximo de pares simultáneos y por sector, pérdida diaria máxima, kill switch por
  drawdown.
- Riesgo de *legging* (una pata ejecutada y la otra no): órdenes combinadas donde el broker las
  soporte, o ejecución secuencial con *rollback*. Conciliación periódica con el broker.

## 9. Hoja de ruta y estado

| Fase | Entregable | Estado |
|---|---|---|
| 0. Higiene y definición | Repo limpio, configuración por entorno | ✔ Hecho. **Falta rotar la `FMP_API_KEY`** (estuvo versionada). PostgreSQL pospuesto |
| 1. Datos e investigación | Precios ajustados, `DailyBar`, `import_prices`, `find_pairs`, calibración | ✔ Código y tests. ⏳ Falta probar `import_prices` contra la API real de FMP y escanear datos reales. ☐ Clustering jerárquico |
| 2. Backtest realista | Simulador con costes, walk-forward, informe, gap de apertura relativo | ☐ |
| 3. Scanner web (solo señales) | Dashboard, scanner, detalle de par, alertas | ☐ |
| 4. Paper trading | Adaptador IBKR, OMS, risk manager, kill switch; ≥ 6–8 semanas en paper | ☐ |
| 5. Real con poco capital | Tamaño mínimo, escalado por reglas, revisión semanal | ☐ |
| 6. Mejoras | Kalman, tripletas o cestas, intradía, lead–lag, otros mercados | ☐ |

**Puerta de la Fase 1:** ¿hay pares estables fuera de muestra? Hoy solo se puede responder en
muestra; la respuesta honesta llega con el walk-forward de la Fase 2.

### Siguientes pasos inmediatos

1. Rotar la clave de FMP y crear `.env` a partir de `.env.example`.
2. Probar `import_prices` con un ticker y una ventana corta (valida el parser contra la API real:
   los nombres de las claves JSON de FMP no se pudieron contrastar al desarrollarlo).
3. Importar un universo (50–100 valores líquidos con sector) y ejecutar `find_pairs`.
4. Fijar los umbrales de éxito de la Fase 2 antes de ver resultados de backtest.

## 10. Riesgos

- **Tests múltiples y sobreajuste:** FDR, validación fuera de muestra y no calibrar sobre toda la
  muestra.
- **Ruptura de la relación** (fusión, resultados, cambio de negocio): stop, *time stop* de 2–3× el
  half-life, re-test rodante y calendario de earnings.
- **Costes:** el pairs trading clásico en EE. UU. tiene poco margen y está muy competido; los
  costes deciden si hay negocio.
- **Datos y regulación:** mostrar datos en tiempo real a terceros exige licencia de mercado, y dar
  señales a otros usuarios puede ser actividad regulada (CNMV / MiFID II). Para uso personal
  normalmente no aplica. Si se opera desde la UE, los ETFs de EE. UU. suelen estar vetados a
  minoristas (PRIIPs): usar acciones o ETFs UCITS.

## 11. Decisiones abiertas (por defecto)

| Decisión | Por defecto |
|---|---|
| Universo | Acciones líquidas de EE. UU. (S&P 500 por sector) |
| Horizonte | Diario en el MVP, intradía después |
| Posiciones | Largo/corto neutral (requiere poder abrir cortos) |
| Datos | FMP (comprobar qué incluye el plan: histórico, intradía, límites); `yfinance` solo para prototipar |
| Broker | Interactive Brokers, primero paper |
| Uso | Personal |
