"""Estrategias disponibles y su construcción a partir de la configuración."""
from __future__ import annotations

from ..config import BacktestConfig
from ..data import MarketData
from .pairs import PairsWalkForward, make_windows, select_pairs
from .reversal import SectorReversal


def prepare_pairs(data: MarketData, config: BacktestConfig, progress=None):
    """Ventanas y selección de pares (la etapa cara, cacheable)."""
    from ..simulator import index_range

    start_idx, end_idx = index_range(data, config)
    rules = config.rules
    windows = make_windows(rules.formation, rules.trading, start_idx, end_idx)
    selections = select_pairs(data.close.iloc[: end_idx + 1], data.groups, windows, rules, progress=progress)
    return windows, selections


def build_strategy(config: BacktestConfig, data: MarketData, *, selections=None, progress=None):
    if config.strategy == 'reversal':
        return SectorReversal(config.rules, data.tickers, data.groups)
    if config.strategy == 'pairs':
        from ..simulator import index_range

        start_idx, end_idx = index_range(data, config)
        windows = make_windows(config.rules.formation, config.rules.trading, start_idx, end_idx)
        if selections is None:
            windows, selections = prepare_pairs(data, config, progress=progress)
        if len(selections) != len(windows):
            raise ValueError('La selección de pares no corresponde a estas ventanas (¿datos o fechas distintos?).')
        return PairsWalkForward(config.rules, data.tickers, windows, selections)
    raise ValueError(f'Estrategia desconocida: {config.strategy}')
