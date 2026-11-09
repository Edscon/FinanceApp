"""Calibración por simulación del test de cointegración: potencia y tamaño.

    python -m engine.calibration --n 504 --seeds 100

Para ventanas de ``n`` sesiones muestra:

* potencia: % de pares con cointegración REAL (según su half-life) que el test detecta;
* tamaño: % de pares SIN cointegración que el test marca por error, tanto con caminos
  independientes como con pares muy correlacionados por un factor común. Debería rondar el
  nivel nominal (5 %): si es mucho mayor, el test fabrica pares; si es menor, es conservador.

Sirve para saber qué puede ver el escáner con una ventana dada (p. ej. comparar 504 y 756
sesiones) antes de fiarse de que un par "no aparezca" o "aparezca".
"""
from __future__ import annotations

import argparse

import numpy as np

from .data.synthetic import cointegrated_pair, factor_walks, random_walk
from .research.stats import pair_cointegration


def _percent(flags: list[bool]) -> float:
    return 100.0 * sum(flags) / len(flags)


def detection_rate(n: int, half_life: float, seeds: int, level: float) -> float:
    """% de pares cointegrados (half-life dado) cuyo p-valor queda por debajo de ``level``."""
    flags = []
    for seed in range(seeds):
        df = cointegrated_pair(n, seed=seed, half_life=half_life)
        flags.append(pair_cointegration(np.log(df['Y']), np.log(df['X'])).pvalue < level)
    return _percent(flags)


def false_positive_rate_independent(n: int, seeds: int, level: float) -> float:
    """% de falsos positivos con dos caminos aleatorios independientes."""
    flags = []
    for seed in range(seeds):
        a = random_walk(n, seed=seed, name='A')
        b = random_walk(n, seed=10_000 + seed, name='B')
        flags.append(pair_cointegration(np.log(a), np.log(b)).pvalue < level)
    return _percent(flags)


def false_positive_rate_common_factor(n: int, seeds: int, level: float) -> float:
    """% de falsos positivos con dos precios correlacionados (~0,8) pero sin cointegración."""
    flags = []
    for seed in range(seeds):
        df = factor_walks(n, seed=seed)
        flags.append(pair_cointegration(np.log(df['C']), np.log(df['D'])).pvalue < level)
    return _percent(flags)


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(
        prog='python -m engine.calibration',
        description='Potencia y tamaño del test de cointegración por simulación.',
    )
    parser.add_argument('--n', type=int, default=504, help='sesiones de la ventana (por defecto 504)')
    parser.add_argument('--seeds', type=int, default=100, help='pares simulados por celda (por defecto 100)')
    parser.add_argument('--level', type=float, default=0.05, help='nivel de significación (por defecto 0.05)')
    parser.add_argument(
        '--half-lives', type=float, nargs='+', default=[3, 5, 7, 10, 15, 20, 30],
        help='half-lives (días) para la tabla de potencia',
    )
    args = parser.parse_args(argv)

    print(f'Ventana de {args.n} sesiones, {args.seeds} pares simulados por fila, nivel {args.level:g}')
    print('\nPotencia (pares con cointegración real detectados):')
    for half_life in args.half_lives:
        rate = detection_rate(args.n, half_life, args.seeds, args.level)
        print(f'  half-life {half_life:>4g} d -> {rate:5.1f} %')
    print('\nTamaño (pares SIN cointegración marcados por error):')
    print(f'  caminos independientes       -> {false_positive_rate_independent(args.n, args.seeds, args.level):5.1f} %')
    print(f'  correlados por factor común  -> {false_positive_rate_common_factor(args.n, args.seeds, args.level):5.1f} %')


if __name__ == '__main__':
    main()
