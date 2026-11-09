"""Ejecución, robustez y presentación de backtests (pegamento entre Django y el motor)."""
from __future__ import annotations

import csv
import io
import math

import numpy as np

from core import charts
from core.jobs import enqueue
from engine.backtest.config import STRATEGY_NAMES, BacktestConfig
from engine.backtest.result import jsonsafe
from engine.backtest.robustness import gate, variants
from engine.backtest.simulator import index_range, run_backtest
from engine.backtest.strategies.pairs import (
    make_windows,
    select_pairs,
    selection_key,
    selections_from_json,
    selections_to_json,
)
from engine.research.pairs import ScanConfig, find_pairs
from engine.research.stats import fit_hedge_ratio, half_life, pair_cointegration
from market.services import load_market_data

from .models import BacktestRun, SelectionCache, Trade

ROBUSTNESS_ORDER = ['costs_x0', 'costs_x1_5', 'costs_x2', 'fill', 'param_low', 'param_high']

LABELS = {
    'lookback': 'Sesiones del gap', 'rebalance': 'Rebalanceo cada (sesiones)', 'vol_window': 'Ventana de volatilidad',
    'liquidity_window': 'Ventana de liquidez', 'gross': 'Exposición bruta', 'max_weight': 'Peso máximo por valor',
    'min_gap': 'Gap mínimo (σ)', 'min_price': 'Precio mínimo', 'min_dollar_volume': 'Volumen mínimo en $',
    'min_group_size': 'Mínimo de valores por grupo', 'no_trade_band': 'Banda sin operar',
    'formation': 'Formación (sesiones)', 'trading': 'Operativa (sesiones)', 'max_pairs': 'Pares máximos',
    'gross_per_pair': 'Bruto por par', 'entry_z': 'Entrada |z|', 'exit_z': 'Salida |z|', 'stop_z': 'Stop |z|',
    'max_hold_hl': 'Time stop (× half-life)', 'require_cross': 'Exigir cruce del umbral', 'beta_min': 'β mínima',
    'beta_max': 'β máxima', 'min_corr': 'Correlación mínima', 'max_p_adj': 'p ajustado máximo',
    'min_half_life': 'Half-life mínimo', 'max_half_life': 'Half-life máximo', 'use_groups': 'Solo dentro de cada grupo',
    'commission_bps': 'Comisión (pb por lado)', 'slippage_bps': 'Deslizamiento (pb por lado)',
    'borrow_bps_annual': 'Préstamo de cortos (pb/año)', 'execution': 'Ejecución', 'capital': 'Capital inicial',
    'start': 'Desde', 'end': 'Hasta',
}
EXECUTION_LABELS = {'next_close': 'Cierre de la sesión siguiente', 'next_open': 'Apertura de la sesión siguiente'}
EXIT_REASONS = {
    'target': 'Reversión (objetivo)', 'stop': 'Stop', 'time': 'Time stop', 'window_end': 'Fin de ventana',
    'data_gap': 'Hueco de datos', 'end_of_data': 'Fin de los datos',
}


def default_name(config: BacktestConfig, universe) -> str:
    return f'{STRATEGY_NAMES[config.strategy]} · {universe.name}'


def create_run(config: BacktestConfig, universe, *, name='', parent=None, variant_key='', variant_label='') -> BacktestRun:
    run = BacktestRun.objects.create(
        name=name or default_name(config, universe),
        strategy=config.strategy,
        universe=universe,
        universe_name=universe.name,
        config=config.to_dict(),
        parent=parent,
        variant_key=variant_key,
        variant_label=variant_label,
    )
    label = f'Backtest «{run.name}»' + (f' · {variant_label}' if variant_label else '')
    job = enqueue('backtest', {'run_id': run.pk}, label=label)
    BacktestRun.objects.filter(pk=run.pk).update(job=job)
    run.job = job
    return run


def execute(run_id: int, progress) -> BacktestRun:
    run = BacktestRun.objects.select_related('universe').get(pk=run_id)
    if run.universe is None:
        raise ValueError('El universo de este backtest ya no existe.')
    config = run.config_obj()
    progress(0.02, 'Cargando precios')
    data = load_market_data(run.universe, end=config.end)
    selections = None
    if config.strategy == 'pairs':
        start_idx, end_idx = index_range(data, config)
        windows = make_windows(config.rules.formation, config.rules.trading, start_idx, end_idx)
        key = selection_key(data.fingerprint(), config.rules, windows)
        cached = SelectionCache.objects.filter(key=key).first()
        if cached:
            selections = selections_from_json(cached.payload)
            progress(0.6, 'Selección de pares reutilizada de un backtest anterior')
        else:
            selections = select_pairs(
                data.close.iloc[: end_idx + 1], data.groups, windows, config.rules,
                progress=lambda f, m: progress(0.05 + 0.55 * f, m),
            )
            SelectionCache.objects.update_or_create(key=key, defaults={'payload': selections_to_json(selections)})
        run.selection_key = key
        sim_progress = lambda f, m: progress(0.6 + 0.4 * f, m)  # noqa: E731
    else:
        sim_progress = lambda f, m: progress(0.05 + 0.95 * f, m)  # noqa: E731
    result = run_backtest(data, config, selections=selections, progress=sim_progress)
    store(run, result)
    return run


def store(run: BacktestRun, result) -> None:
    run.metrics = jsonsafe(result.metrics)
    run.daily = jsonsafe(result.daily())
    run.windows = jsonsafe(result.windows)
    run.attribution = jsonsafe(result.attribution)
    run.warnings = list(result.warnings)
    run.fingerprint = result.fingerprint
    run.engine_version = result.engine_version
    run.save()
    run.trades.all().delete()
    Trade.objects.bulk_create(
        [
            Trade(
                run=run, trade_id=t.trade_id, window=t.window, y=t.y, x=t.x, direction=t.direction, beta=t.beta,
                half_life=t.half_life, signal_date=t.signal_date, entry_date=t.entry_date,
                exit_signal_date=t.exit_signal_date, exit_date=t.exit_date, exit_reason=t.exit_reason,
                entry_z=t.entry_z, exit_z=t.exit_z if math.isfinite(t.exit_z) else None,
                holding_sessions=t.holding_sessions, entry_gross_notional=t.entry_gross_notional,
                gross_pnl=t.gross_pnl, commission=t.commission, slippage=t.slippage, borrow=t.borrow, net_pnl=t.net_pnl,
            )
            for t in result.trades
        ],
        batch_size=500,
    )


def launch_robustness(run: BacktestRun) -> int:
    existing = set(run.variants.values_list('variant_key', flat=True))
    launched = 0
    for key, label, config in variants(run.config_obj()):
        if key not in existing:
            create_run(config, run.universe, name=run.name, parent=run, variant_key=key, variant_label=label)
            launched += 1
    return launched


def robustness_summary(run: BacktestRun) -> dict | None:
    children = {v.variant_key: v for v in run.variants.select_related('job')}
    if not children:
        return None
    rows = [{'label': 'Configuración base', 'run': run, 'metrics': run.metrics, 'base': True}]
    for key in ROBUSTNESS_ORDER:
        if key in children:
            child = children[key]
            rows.append({'label': child.variant_label, 'run': child, 'metrics': child.metrics if child.is_done else {}})
    done = all(child.is_done for child in children.values())
    result = {'rows': rows, 'done': done, 'active': any(child.is_active for child in children.values())}
    if done:
        result['gate'] = gate({k: c.metrics for k, c in children.items()} | {'base': run.metrics})
    return result


def trials_count(run: BacktestRun) -> int:
    return BacktestRun.objects.filter(parent__isnull=True, universe_id=run.universe_id).count()


def equity_chart(run: BacktestRun) -> dict:
    daily = run.daily
    dates, equity = daily['dates'], daily['equity']
    items = [charts.series(dates, equity, 'Estrategia', charts.GREEN, fmt='money')]
    bench = daily.get('benchmark')
    if bench:
        first = next((b for b in bench if b), None)
        if first:
            scale = equity[0] / first
            name = run.universe.benchmark if run.universe else 'Benchmark'
            items.append(charts.series(dates, [b * scale if b else None for b in bench], f'{name} (misma inversión)',
                                       charts.SLATE, fmt='money', width=1))
    items.append(charts.series(dates, charts.drawdown(equity), 'Drawdown', charts.ROSE, pane=1, kind='area', fmt='pct'))
    return charts.chart(*items, stretch=[3, 1])


def exposure_chart(run: BacktestRun) -> dict:
    daily = run.daily
    equity = daily['equity']
    gross = [g / e if e else None for g, e in zip(daily['gross'], equity)]
    net = [n / e if e else None for n, e in zip(daily['net'], equity)]
    return charts.chart(
        charts.series(daily['dates'], gross, 'Exposición bruta', charts.BLUE, fmt='pct'),
        charts.series(daily['dates'], net, 'Exposición neta', charts.AMBER, fmt='pct', width=1),
    )


def heat_style(value, scale=0.05) -> str:
    if value is None:
        return ''
    alpha = min(0.85, abs(value) / scale * 0.85)
    color = '52, 211, 153' if value > 0 else '251, 113, 133'
    return f'background: rgba({color}, {alpha:.2f})'


def monthly_rows(metrics: dict) -> list[dict]:
    monthly = metrics.get('monthly') or {}
    yearly = metrics.get('yearly') or {}
    rows = []
    for year in sorted(monthly, key=int):
        months = monthly[year]
        cells = []
        for month in range(1, 13):
            value = months.get(str(month), months.get(month))
            cells.append({'value': value, 'style': heat_style(value)})
        total = yearly.get(str(year), yearly.get(year))
        rows.append({'year': year, 'cells': cells, 'total': total, 'total_style': heat_style(total, 0.2)})
    return rows


def config_rows(config: dict) -> list[tuple[str, str]]:
    rows = [
        (LABELS['execution'], EXECUTION_LABELS.get(config.get('execution'), config.get('execution'))),
        (LABELS['capital'], f"{config.get('capital', 0):,.0f} $".replace(',', '.')),
        (LABELS['start'], config.get('start') or 'Inicio de los datos'),
        (LABELS['end'], config.get('end') or 'Fin de los datos'),
    ]
    for section in ('rules', 'costs'):
        for key, value in (config.get(section) or {}).items():
            if isinstance(value, bool):
                value = 'Sí' if value else 'No'
            rows.append((LABELS.get(key, key), value))
    return rows


def ranked_tickers(run: BacktestRun, n=10):
    items = sorted(((t, v) for t, v in (run.attribution.get('by_ticker') or {}).items()), key=lambda kv: kv[1]['net'])
    return items[::-1][:n], items[:n]


def groups_table(run: BacktestRun) -> list[tuple[str, dict, float]]:
    groups = run.attribution.get('by_group') or {}
    top = max((abs(v['net']) for v in groups.values()), default=0) or 1
    return [(name, v, 100 * abs(v['net']) / top) for name, v in sorted(groups.items(), key=lambda kv: -kv[1]['net'])]


def trades_csv(run: BacktestRun) -> str:
    out = io.StringIO()
    writer = csv.writer(out)
    fields = ['trade_id', 'window', 'y', 'x', 'direction', 'beta', 'half_life', 'signal_date', 'entry_date',
              'exit_signal_date', 'exit_date', 'exit_reason', 'entry_z', 'exit_z', 'holding_sessions',
              'entry_gross_notional', 'gross_pnl', 'commission', 'slippage', 'borrow', 'net_pnl']
    writer.writerow(fields)
    for trade in run.trades.all():
        writer.writerow([getattr(trade, f) for f in fields])
    return out.getvalue()


def equity_csv(run: BacktestRun) -> str:
    out = io.StringIO()
    writer = csv.writer(out)
    daily = run.daily
    writer.writerow(['date', 'equity', 'cash', 'gross_exposure', 'net_exposure', 'positions', 'benchmark'])
    bench = daily.get('benchmark') or [None] * len(daily['dates'])
    for row in zip(daily['dates'], daily['equity'], daily['cash'], daily['gross'], daily['net'], daily['n_positions'], bench):
        writer.writerow(row)
    return out.getvalue()


def scan(universe, as_of=None, lookback=504, min_corr=0.7, max_p=0.05):
    data = load_market_data(universe, end=as_of)
    config = ScanConfig(lookback=lookback, min_corr=min_corr, max_p_adj=max_p)
    return find_pairs(data.close, data.groups or None, config)


def pair_analysis(universe, y: str, x: str, as_of=None, lookback=504, holdout=1) -> dict:
    members = set(universe.tickers())
    for ticker in (y, x):
        if ticker not in members:
            raise ValueError(f'{ticker or "(vacío)"} no está en este universo.')
    data = load_market_data(universe, end=as_of, tickers=[y, x])
    if y not in data.close or x not in data.close:
        raise ValueError('No hay precios de los dos valores en este universo.')
    window = data.close[[y, x]].dropna().iloc[-lookback:]
    if len(window) < 60:
        raise ValueError('Muy poca historia común entre los dos valores.')
    log_prices = np.log(window)
    formation = log_prices.iloc[: len(log_prices) - holdout] if holdout else log_prices
    fit = fit_hedge_ratio(formation[y], formation[x])
    spread_all = log_prices[y] - fit.alpha - fit.beta * log_prices[x]
    mean, sigma = float(fit.spread.mean()), float(fit.spread.std(ddof=1))
    z = (spread_all - mean) / sigma
    dates = [d.date().isoformat() for d in window.index]
    returns = log_prices.diff().dropna()
    stats = {
        'alpha': fit.alpha, 'beta': fit.beta, 'half_life': half_life(fit.spread),
        'pvalue': pair_cointegration(formation[y], formation[x]).pvalue,
        'corr': float(returns[y].corr(returns[x])), 'z': float(z.iloc[-1]), 'sigma': sigma,
        'start': dates[0], 'end': dates[-1], 'sessions': len(dates),
    }
    sigma_lines = [{'price': mean + k * sigma, 'color': color, 'title': f'{k:+d}σ', 'style': 2}
                   for k, color in ((3, charts.ROSE), (2, charts.AMBER), (1, charts.SLATE), (-1, charts.SLATE),
                                    (-2, charts.AMBER), (-3, charts.ROSE))]
    payload = charts.chart(
        charts.series(dates, (100 * window[y] / window[y].iloc[0]).tolist(), y, charts.GREEN),
        charts.series(dates, (100 * window[x] / window[x].iloc[0]).tolist(), x, charts.BLUE),
        charts.series(dates, spread_all.tolist(), 'Spread', charts.VIOLET, pane=1, fmt='ratio',
                      lines=[{'price': mean, 'color': charts.SLATE, 'title': 'media', 'style': 0}] + sigma_lines),
        charts.series(dates, z.tolist(), 'z', charts.AMBER, pane=2, fmt='ratio',
                      lines=[{'price': 2, 'color': charts.ROSE, 'title': '+2', 'style': 2},
                             {'price': -2, 'color': charts.ROSE, 'title': '−2', 'style': 2}]),
        stretch=[2, 2, 1],
    )
    return {'stats': stats, 'chart': payload}
