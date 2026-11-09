/* Renderizador genérico de gráficos con TradingView Lightweight Charts v5.
   Uso: <div class="chart" data-chart data-source="id-del-json"></div> + {{ datos|json_script:"id-del-json" }}
   Formato de los datos: ver core/charts.py. */
(function () {
  'use strict';

  const FORMATS = {
    money: { type: 'custom', formatter: (v) => Math.round(v).toLocaleString('es-ES') + ' $', minMove: 1 },
    pct: { type: 'custom', formatter: (v) => (v * 100).toLocaleString('es-ES', { maximumFractionDigits: 1 }) + ' %', minMove: 0.0001 },
    ratio: { type: 'price', precision: 2, minMove: 0.01 },
    price: { type: 'price', precision: 2, minMove: 0.01 },
    volume: { type: 'volume' },
  };

  function seriesType(LC, kind) {
    if (kind === 'area') return LC.AreaSeries;
    if (kind === 'histogram') return LC.HistogramSeries;
    return LC.LineSeries;
  }

  function hexToRgba(hex, alpha) {
    const n = parseInt(hex.slice(1), 16);
    return `rgba(${(n >> 16) & 255}, ${(n >> 8) & 255}, ${n & 255}, ${alpha})`;
  }

  function render(el) {
    if (el.dataset.rendered || !window.LightweightCharts) return;
    const source = document.getElementById(el.dataset.source);
    if (!source) return;
    const spec = JSON.parse(source.textContent);
    const LC = window.LightweightCharts;
    const chart = LC.createChart(el, {
      autoSize: true,
      layout: {
        background: { type: 'solid', color: 'transparent' },
        textColor: '#94a3b8',
        fontSize: 11,
        panes: { separatorColor: '#1e293b', separatorHoverColor: '#334155' },
        attributionLogo: false,
      },
      grid: { vertLines: { color: 'rgba(30, 41, 59, 0.55)' }, horzLines: { color: 'rgba(30, 41, 59, 0.55)' } },
      rightPriceScale: { borderColor: '#1e293b' },
      timeScale: { borderColor: '#1e293b' },
      crosshair: { mode: 0 },
      localization: { locale: 'es-ES' },
    });

    spec.series.forEach((s) => {
      const options = {
        color: s.color,
        lineWidth: s.width || 2,
        priceLineVisible: false,
        lastValueVisible: s.type !== 'histogram',
        title: s.title || '',
        priceFormat: FORMATS[s.format] || FORMATS.price,
      };
      if (s.type === 'area') {
        options.lineColor = s.color;
        options.topColor = hexToRgba(s.color, 0.05);
        options.bottomColor = hexToRgba(s.color, 0.35);
        options.invertFilledArea = true;
      }
      if (s.type === 'histogram') options.color = hexToRgba(s.color, 0.5);
      const series = chart.addSeries(seriesType(LC, s.type), options, s.pane || 0);
      series.setData(s.data.map(([time, value]) => (value === null ? { time } : { time, value })));
      (s.lines || []).forEach((line) =>
        series.createPriceLine({
          price: line.price,
          color: line.color,
          lineWidth: 1,
          lineStyle: line.style === undefined ? 2 : line.style,
          axisLabelVisible: true,
          title: line.title || '',
        })
      );
    });

    (spec.stretch || []).forEach((factor, i) => {
      const pane = chart.panes()[i];
      if (pane) pane.setStretchFactor(factor);
    });
    chart.timeScale().fitContent();
    el.dataset.rendered = '1';
  }

  function renderAll(root) {
    (root || document).querySelectorAll('[data-chart]').forEach(render);
  }

  document.addEventListener('DOMContentLoaded', () => renderAll());
  document.addEventListener('htmx:afterSettle', (event) => renderAll(event.target));
})();
