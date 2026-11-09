# FinanceApp

App web local para **hacer backtesting** de estrategias de *gaps* entre valores correlacionados: cuando
un valor se separa de los que normalmente se mueven con él, ¿compensa apostar a que vuelve? Solo
backtesting: no hay broker ni órdenes reales.

**Resultado con datos reales (2010–2026):** ninguna de las estrategias probadas gana dinero de forma
fiable tras costes. Detalle, fuentes y cifras en [docs/ESTRATEGIAS.md](docs/ESTRATEGIAS.md).

## Qué incluye

- **Datos:**
  - precios diarios ajustados desde Yahoo Finance (gratis, sin clave), FMP o CSV;
  - dos universos predefinidos: 144 grandes valores de EE. UU. por sector y 37 ETFs sectoriales;
  - datos de demostración sintéticos para probar sin internet.
- **Estrategias:**
  - **reversión intra-sector** (principal; la de mejor evidencia publicada);
  - **pares cointegrados en walk-forward** (comparación).
- **Motor de backtest:**
  - ejecución en la sesión siguiente;
  - costes de comisión, deslizamiento y préstamo de cortos;
  - métricas con el error estándar del Sharpe y el coste de break-even;
  - variantes de robustez y una puerta de decisión;
  - tests que comprueban que nunca mira al futuro.
- **Web:** panel, datos, universos, escáner de pares con gráfico del spread, backtests con gráficos,
  comparar y exportar a CSV.

## Puesta en marcha

```bash
python -m venv .venv
source .venv/bin/activate              # Windows (PowerShell): .venv\Scripts\Activate.ps1
pip install -r requirements.txt
python manage.py migrate
python manage.py runserver             # http://127.0.0.1:8000
```

Primer uso, en la web:
1. **Datos → Cargar datos de demostración**, para probar sin internet.
2. O **Universos → Crear predefinidos** y luego **Descargar precios (Yahoo)**. Tarda ~1,5 minutos
   para 185 valores desde 2010.
3. **Backtests → Nuevo**, con los parámetros por defecto (fijados antes de ver datos). Después,
   **Ejecutar variantes de robustez**.

Los trabajos largos (importar, backtests) se ejecutan en segundo plano dentro del propio servidor.
Si prefieres un proceso aparte: `JOBS_MODE=external` y `python manage.py run_worker`.

### Desde la línea de comandos

```bash
python manage.py load_demo                                   # datos sintéticos
python manage.py import_prices --presets --from 2010-01-01  # universos predefinidos desde Yahoo
python manage.py import_prices AAPL MSFT --provider fmp      # FMP (requiere FMP_API_KEY en .env)
python manage.py run_backtest "US grandes valores" --strategy reversal --robustness
python -m engine.backtest --demo                             # motor sin Django, sobre datos sintéticos
python -m engine.calibration --n 504                         # potencia del test de cointegración
```

## Antes de fiarte de un resultado

- Los universos son los de hoy: faltan las empresas que quebraron (sesgo de supervivencia), lo que
  infla una estrategia que compra perdedores.
- Mira el resultado con costes ×1,5 y ×2 y el coste de break-even.
- Cada configuración que pruebas sobre el mismo periodo hace más probable encontrar algo bonito por
  azar: la app cuenta cuántas llevas.

## Tests

```bash
python manage.py test        # 171 tests: motor y web
```

## Estructura

```
engine/          Python puro (sin Django): proveedores, estadística, motor de backtest, estrategias
  backtest/      config, datos, vista truncada, simulador, métricas, robustez, estrategias
core/            cola de trabajos, layout, panel
market/          valores, barras diarias, universos, importación, presets, demo
backtests/       ejecuciones, operaciones, formularios, vistas, escáner y par
templates/       plantillas (HTMX + Tailwind)
static/          CSS compilado, gráficos (Lightweight Charts) y htmx en static/vendor
docs/            PLAN.md (plan y estado) y ESTRATEGIAS.md (investigación y veredicto)
```

Para cambiar estilos o actualizar librerías JS: `npm install`, `npm run build:css` y `npm run vendor`.
No hace falta Node para usar la app.

> Nada de esto es asesoramiento financiero. Un backtest no garantiza resultados futuros.
