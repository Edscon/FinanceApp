"""Variantes de robustez de un backtest y la puerta de decisión de docs/PLAN.md.

Puerta: Sharpe neto > 1 con costes x1,5. Como los parámetros por defecto se fijaron antes de ver
datos y la selección de pares es walk-forward, el periodo entero cuenta como fuera de muestra
respecto a los hiperparámetros; si se ajustan mirando resultados, deja de serlo.
"""
from __future__ import annotations

import math

from .config import BacktestConfig, Execution

GATE_SHARPE = 1.0


def variants(config: BacktestConfig) -> list[tuple[str, str, BacktestConfig]]:
    """(clave, etiqueta, configuración) de cada variante."""
    other = Execution.NEXT_OPEN if config.execution == Execution.NEXT_CLOSE else Execution.NEXT_CLOSE
    out = [
        ('costs_x1_5', 'Costes ×1,5', config.replace(costs=config.costs.scaled(1.5))),
        ('costs_x2', 'Costes ×2', config.replace(costs=config.costs.scaled(2.0))),
        ('costs_x0', 'Sin costes (bruto)', config.replace(costs=config.costs.scaled(0.0))),
        ('fill', 'Ejecución a la apertura' if other == Execution.NEXT_OPEN else 'Ejecución al cierre',
         config.replace(execution=other)),
    ]
    rules = config.rules
    if config.strategy == 'reversal':
        low, high = max(1, round(rules.lookback * 0.8)), max(2, round(rules.lookback * 1.2))
        if low == rules.lookback:
            low = max(1, rules.lookback - 1)
        if high == rules.lookback:
            high = rules.lookback + 1
        out += [
            ('param_low', f'Gap de {low} sesiones', config.with_rules(lookback=low)),
            ('param_high', f'Gap de {high} sesiones', config.with_rules(lookback=high)),
        ]
    else:
        low, high = round(rules.entry_z * 0.8, 2), round(rules.entry_z * 1.2, 2)
        if low > rules.exit_z:
            out.append(('param_low', f'Entrada |z| ≥ {low:g}', config.with_rules(entry_z=low)))
        if high < rules.stop_z:
            out.append(('param_high', f'Entrada |z| ≥ {high:g}', config.with_rules(entry_z=high)))
    return out


def gate(variant_metrics: dict[str, dict]) -> dict:
    """¿Supera la puerta? ``variant_metrics``: clave de variante -> métricas."""
    stressed = variant_metrics.get('costs_x1_5') or {}
    sharpe = stressed.get('sharpe')
    passed = sharpe is not None and math.isfinite(sharpe) and sharpe > GATE_SHARPE
    net_positive = [
        (m.get('total_return') or 0) > 0 for key, m in variant_metrics.items() if key != 'costs_x0'
    ]
    return {
        'threshold': GATE_SHARPE,
        'sharpe_costs_x1_5': sharpe,
        'passed': bool(passed),
        'all_variants_positive': bool(net_positive) and all(net_positive),
    }
