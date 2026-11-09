# Estrategias: investigación, elección y resultado con datos reales

> Resumen: con datos reales de 2010 a 2026 y costes realistas, **ninguna** de las estrategias
> probadas supera la puerta de decisión. La reversión intra-sector tiene una ventaja **bruta**
> pequeña pero consistente (Sharpe 0,3–0,45), y los costes de operar se la comen. Los pares
> cointegrados apenas encuentran pares estables y, cuando operan, pierden. Según el plan, no se
> construye ejecución con estas estrategias tal cual.

## 1. La pregunta

Detectar *gaps* (separaciones anómalas) entre valores que normalmente se mueven juntos y apostar a
que se cierran. Se investigaron las variantes publicadas y se probó la de mejor evidencia neta de
costes, con la clásica de pares como comparación.

## 2. Qué dice la literatura

| Candidata | Evidencia | Decisión |
|---|---|---|
| **Reversión relativa intra-sector**: cuánto se separó un valor de su sector, largo en los rezagados y corto en los adelantados | La parte residual de la reversión rinde ~4 veces la reversión clásica (Da, Liu & Schaumburg, *Management Science* 2014). La reversión intra-industria es generalizada, también en valores grandes y líquidos, y más fuerte tras caídas y en épocas volátiles (Hameed & Mian, *JFQA* 2015). La reversión residual es significativa neta de costes incluso en grandes valores (Robeco, 2011) | **Principal** |
| Pares cointegrados / método de distancia | La rentabilidad cae desde los 2000. Con costes realistas, los rendimientos en exceso casi desaparecen salvo en crisis (estudio S&P 500 1980–2014; Do & Faff). Hay evidencia reciente mixta, p. ej. pares de ETFs con Sharpe neto 1,43 (Tokat & Hayrullahoğlu 2021) | **Comparación** |
| Residuo frente al ETF sectorial (Avellaneda & Lee 2010) | Sharpe 1,1 en 1997–2007, degradándose desde 2002. Una réplica en 2013–2016 da Sharpe 0,54 | Variante futura |
| Gap de apertura (overnight → intradía) | Rendimiento bruto enorme con ~100 % de rotación diaria. Ya no se observa en el S&P 500 (Branch & Ma 2018) y se concentra en valores pequeños e ilíquidos. Ejecutar al precio de apertura es optimista | **Descartada** |
| Revisión general | Distancia, cointegración, series temporales, cópulas y otros enfoques (Krauss, *J. Econ. Surveys* 2017) | Contexto |

Fuentes consultadas:
- Avellaneda & Lee: https://math.nyu.edu/~avellane/AvellanedaLeeStatArb071008.pdf
- Da, Liu & Schaumburg (NY Fed SR 513): https://newyorkfed.org/medialibrary/media/research/staff_reports/sr513.html
- Hameed & Mian: https://ideas.repec.org/a/cup/jfinqa/v50y2015i1-2p89-117_00.html
- Robeco, reversión residual: https://www.robeco.com/jp-jp/insights/2011/11/short-term-residual-reversal
- Krauss (2017): https://econpapers.repec.org/RePEc:bla:jecsur:v:31:y:2017:i:2:p:513-545
- Pares y costes, 1980–2014: https://springerlink.bibliotecabuap.elogim.com/10.1007/s11408-016-0280-x
- Pares con el método de distancia en el S&P 500: https://ideas.repec.org/a/taf/applec/v47y2015i6p599-613.html
- Reglas de pares y declive (cita a Do & Faff): https://arxiv.org/pdf/2010.01157
- Pares de ETFs, 2007–2021: https://gcris.etu.edu.tr/handle/20.500.11851/8588
- Reversión overnight/intradía (resumen de CXO Advisory): https://www.cxoadvisory.com/technical-trading/overnightintraday-return-reversal-trading/
- Réplica de Avellaneda & Lee en 2013–2016 (Stanford): https://web.stanford.edu/class/msande448/2017/Final/Reports/gr5.pdf

Varias de estas cifras proceden de resúmenes secundarios. Antes de citarlas fuera de este
proyecto, contrástalas con los artículos originales.

## 3. Cómo se probó

- **Datos:** Yahoo Finance, precios ajustados, 2010-01 a 2026-11.
  - «US grandes valores»: 144 acciones en 11 sectores GICS.
  - «ETFs sectoriales e industriales»: 37 ETFs en 8 grupos.
  - SPY como referencia.
- **Ejecución:** señal al cierre de t y ejecución al cierre de t+1. El motor impide mirar al futuro
  por construcción y lo comprueba con tests de perturbación.
- **Costes por defecto:**
  - 1 pb de comisión y 5 pb de deslizamiento por lado;
  - 50 pb al año de préstamo sobre los cortos;
  - sin interés sobre la caja.
- **Parámetros fijados antes de ver datos:**
  - Reversión: gap de 5 sesiones, rebalanceo cada 5, volatilidad de 60, exposición bruta 1,0 y
    tope del 5 % por valor.
  - Pares: formación de 504 sesiones, operativa de 126, entrada con |z| ≥ 2, salida con |z| ≤ 0,5,
    stop con |z| ≥ 4, time stop de 3 half-lives y half-life de 30 como máximo.
- **Robustez:** costes ×0 / ×1,5 / ×2, el otro modo de ejecución y el parámetro principal ±20 %.
- **Puerta (PLAN.md):** Sharpe neto > 1 con costes ×1,5.
- **Prueba adicional:** la reversión **mensual** (gap y rebalanceo de 21 sesiones) se probó como
  una única hipótesis alternativa, declarada antes de ejecutarla. Es la especificación de los
  artículos citados, mientras que el valor por defecto elegido era semanal. Cuenta como un test más
  a efectos de tests múltiples.

## 4. Resultados

Sharpe anualizado ± su error estándar (aprox. 0,25 en 16 años). Entre paréntesis, el CAGR.

| Estrategia · universo | Bruto (sin costes) | Neto (costes por defecto) | Neto con costes ×1,5 | Rotación anual | Break-even (pb/lado) |
|---|---|---|---|---|---|
| Reversión semanal · grandes valores | 0,45 (+2,0 %) | −0,53 (−2,6 %) | −1,01 | 72× | 2,6 |
| Reversión semanal · ETFs | 0,33 (+1,4 %) | −0,62 (−2,8 %) | −1,08 | 67× | 1,7 |
| Reversión mensual · grandes valores | 0,31 (+1,3 %) | 0,03 (+0,0 %) | −0,11 | 17× | 6,1 |
| Reversión mensual · ETFs | 0,31 (+1,3 %) | 0,04 (+0,1 %) | −0,09 | 16× | 6,5 |
| Pares · grandes valores | −0,34 (−0,2 %) | −0,41 (−0,2 %) | −0,44 | 0,5× | negativo |
| Pares · ETFs | sin operaciones | sin operaciones | — | 0× | — |

Detalles relevantes:
- **Reversión semanal, grandes valores:**
  - En bruto gana en 11 de 17 años, con el mejor año en 2020 (+11,7 %, coherente con «más fuerte
    en épocas volátiles»).
  - La ventaja se debilita: Sharpe bruto 0,62 en la primera mitad y 0,38 en la segunda.
  - Es neutral al mercado (beta ≈ 0,05).
  - Los costes suman 2,2 veces el beneficio bruto.
  - Ninguna variante de robustez es positiva en neto.
- **Reversión mensual:** los costes se comen el 98 % del beneficio bruto. Con un gap de 17
  sesiones sale algo mejor (Sharpe neto 0,22), pero elegir ahora esa variante sería sobreajuste.
- **Pares, grandes valores:**
  - Con la corrección por tests múltiples y half-life ≤ 30, solo 6 de 30 ventanas tienen algún par.
  - Hubo 19 operaciones, con un 32 % de aciertos.
  - Los 6 stops (−4.549 $) dominan el resultado, porque las relaciones se rompen.
- **Pares, ETFs:** en 29 de 30 ventanas ningún par supera los filtros, y en la restante nunca se
  dio la señal de entrada.

## 5. Veredicto

1. **No hay ventaja neta explotable** con estas estrategias en valores líquidos de EE. UU. entre
   2010 y 2026, con costes de un minorista con buen broker. La puerta no se supera en ningún caso.
2. **La ventaja bruta de la reversión es real pero pequeña.** Está en torno a 1,3–1,8 errores
   estándar, así que es poco concluyente, y además está inflada por el sesgo de supervivencia
   (el universo es el de hoy).
3. **Cuánto tendrían que costar las operaciones para que salgan las cuentas:** menos de ~2,5 pb
   por lado (semanal) o ~6 pb (mensual), y aun así el Sharpe neto quedaría muy por debajo de 1.
4. **El pairs trading clásico con filtros estadísticos serios casi no encuentra pares estables.**
   Las relaciones que encuentra se rompen.

## 6. Si se quiere seguir investigando

Cada prueba adicional sobre el mismo periodo aumenta el riesgo de encontrar algo bonito por azar.
Conviene declarar la hipótesis antes de ejecutarla y reservar un periodo de validación final.

- **Costes reales por valor** (diferencial medio): los megacaps pueden costar 1–2 pb y cambiar el
  veredicto de la versión mensual.
- **Reducir la rotación:** bandas sin operar más anchas, pesos por cuantiles o solo los gaps
  extremos (`min_gap`).
- **Residuos frente a factores** (Avellaneda & Lee, o sector + mercado) en lugar de frente a la
  media del grupo.
- **Universos con más dispersión:** valores medianos o Europa. Ojo: suben los costes.
- **Datos sin sesgo de supervivencia:** imprescindibles antes de fiarse de un resultado positivo.

## 7. Cómo reproducirlo

```bash
python manage.py import_prices --presets --from 2010-01-01
python manage.py run_backtest "US grandes valores" --strategy reversal --robustness
python manage.py run_backtest "ETFs sectoriales e industriales" --strategy reversal --robustness
python manage.py run_backtest "US grandes valores" --strategy pairs --robustness
python manage.py run_backtest "ETFs sectoriales e industriales" --strategy pairs --robustness
```

La versión mensual se lanza desde la web (Backtests → Nuevo): «Sesiones del gap» = 21 y
«Rebalanceo cada» = 21. Las cifras pueden variar ligeramente si Yahoo revisa sus ajustes históricos.
Cada resultado guarda la huella de los datos con los que se calculó.
