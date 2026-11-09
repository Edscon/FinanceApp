"""Datos para el renderizador genérico de gráficos (static/js/charts.js).

Un gráfico es ``{'series': [...], 'stretch': [...]}``; cada serie:
``{'title', 'color', 'type': line|area|histogram, 'pane', 'format': price|money|pct|ratio,
'data': [[fecha, valor], ...], 'lines': [{'price', 'color', 'title', 'style'}]}``.
"""
import math

GREEN = '#34d399'
BLUE = '#60a5fa'
SLATE = '#94a3b8'
AMBER = '#fbbf24'
ROSE = '#fb7185'
VIOLET = '#a78bfa'
PALETTE = [GREEN, BLUE, AMBER, VIOLET, ROSE, '#2dd4bf', '#f472b6', '#a3e635']


def _clean(value):
    if value is None:
        return None
    value = float(value)
    return value if math.isfinite(value) else None


def series(dates, values, title, color, *, pane=0, kind='line', fmt='price', width=2, lines=None):
    return {
        'title': title,
        'color': color,
        'type': kind,
        'pane': pane,
        'format': fmt,
        'width': width,
        'data': [[str(d), _clean(v)] for d, v in zip(dates, values)],
        'lines': lines or [],
    }


def chart(*series_list, stretch=None):
    return {'series': list(series_list), 'stretch': stretch or []}


def drawdown(values):
    out, peak = [], -math.inf
    for value in values:
        peak = max(peak, value)
        out.append(value / peak - 1 if peak > 0 else 0.0)
    return out
