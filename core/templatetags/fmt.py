"""Formato de números al estilo español (1.234,5) para las plantillas.

``tone`` devuelve las clases CSS ``pos``/``neg`` definidas en static/css/input.css (no son
utilidades de Tailwind, así que no dependen del escaneo de plantillas).
"""
import math

from django import template

register = template.Library()

DASH = '—'


def _number(value):
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _es(number, decimals):
    text = f'{number:,.{decimals}f}'
    return text.translate(str.maketrans(',.', '.,'))


@register.filter
def num(value, decimals=2):
    number = _number(value)
    return DASH if number is None else _es(number, int(decimals))


@register.filter
def signed(value, decimals=2):
    number = _number(value)
    if number is None:
        return DASH
    return ('+' if number > 0 else '') + _es(number, int(decimals))


@register.filter
def pct(value, decimals=1):
    """Fracción -> porcentaje: 0.123 -> '12,3 %'."""
    number = _number(value)
    return DASH if number is None else f'{_es(100 * number, int(decimals))} %'


@register.filter
def signed_pct(value, decimals=1):
    number = _number(value)
    if number is None:
        return DASH
    return ('+' if number > 0 else '') + f'{_es(100 * number, int(decimals))} %'


@register.filter
def money(value, decimals=0):
    number = _number(value)
    return DASH if number is None else f'{_es(number, int(decimals))} $'


@register.filter
def tone(value):
    number = _number(value)
    if number is None or number == 0:
        return ''
    return 'pos' if number > 0 else 'neg'


@register.filter
def at(sequence, index):
    try:
        return sequence[int(index) % len(sequence)]
    except (TypeError, ValueError, ZeroDivisionError):
        return None


@register.filter
def get(mapping, key):
    try:
        return mapping.get(key)
    except AttributeError:
        return None
