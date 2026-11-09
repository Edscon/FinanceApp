"""Formato de texto del informe de un escaneo."""
from __future__ import annotations

from .pairs import PairResult, ScanReport, trade_legs

SORT_KEYS = {
    'z': lambda r: -abs(r.z),  # mayor gap primero
    'p': lambda r: r.p_adj,  # más significativo primero
    'hl': lambda r: r.half_life,  # reversión más rápida primero
    'corr': lambda r: -r.corr,  # más correlacionado primero
}

DISCLAIMER = (
    'Aviso: resultados en muestra, sin validación fuera de muestra ni costes '
    '(Fase 2). No es una recomendación de inversión.'
)


def _fmt_p(value: float) -> str:
    return '<0.0001' if value < 1e-4 else f'{value:.4f}'


def _row(pair: PairResult, entry_z: float) -> tuple[str, ...]:
    legs = trade_legs(pair, entry_z)
    signal = f'CORTO {legs[0]} / LARGO {legs[1]}' if legs else '-'
    return (
        f'{pair.y} / {pair.x}',
        pair.group or '-',
        f'{pair.corr:.2f}',
        f'{pair.beta:.2f}',
        _fmt_p(pair.p_adj),
        f'{pair.half_life:.1f}',
        f'{pair.z:+.2f}',
        'si' if pair.stable else 'no',
        signal,
    )


def format_report(report: ScanReport, *, entry_z: float = 2.0, sort: str = 'z', top: int = 30) -> str:
    """Devuelve el informe como texto: cabecera, tabla de pares y aviso."""
    if sort not in SORT_KEYS:
        raise ValueError(f'sort debe ser uno de {sorted(SORT_KEYS)}.')
    cfg = report.config
    if cfg.holdout == 1:
        reference = 'parámetros estimados sin la última sesión'
    elif cfg.holdout > 1:
        reference = f'parámetros estimados sin las últimas {cfg.holdout} sesiones'
    else:
        reference = 'parámetros estimados con toda la ventana'
    lines = [
        f'Escaneo a {report.as_of} · {report.n_obs} sesiones · {len(report.tickers_used)} tickers · '
        f'{report.n_tested} pares probados · {len(report.pairs)} superan el filtro',
        f'(correlación >= {cfg.min_corr:.2f}, p ajustado (FDR) <= {cfg.max_p_adj:.2f}, '
        f'half-life {cfg.min_half_life:g}-{cfg.max_half_life:g} días; gap si |z| >= {entry_z:g}; '
        f'{reference})',
    ]
    if report.tickers_dropped:
        dropped = ', '.join(f'{t} ({why})' for t, why in sorted(report.tickers_dropped.items()))
        lines.append(f'Descartados por datos: {dropped}')

    shown = sorted(report.pairs, key=SORT_KEYS[sort])[: max(top, 0)]
    if not shown:
        lines += ['', 'Ningún par supera el filtro con estos parámetros.']
    else:
        header = ('PAR (y / x)', 'GRUPO', 'CORR', 'BETA', 'P_ADJ', 'HL(d)', 'Z', 'ESTABLE', 'SEÑAL')
        body = [_row(pair, entry_z) for pair in shown]
        widths = [max(len(header[i]), *(len(row[i]) for row in body)) for i in range(len(header))]
        left_aligned = {0, 1, 8}

        def fmt(row):
            cells = [
                cell.ljust(widths[i]) if i in left_aligned else cell.rjust(widths[i])
                for i, cell in enumerate(row)
            ]
            return '  '.join(cells).rstrip()

        lines += ['', fmt(header), fmt(tuple('-' * w for w in widths))]
        lines += [fmt(row) for row in body]
        if len(report.pairs) > len(shown):
            lines.append(f'... y {len(report.pairs) - len(shown)} más (sube --top para verlos).')

    lines += ['', DISCLAIMER]
    return '\n'.join(lines)
