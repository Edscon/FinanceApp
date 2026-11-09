"""Lista pares correlacionados y cointegrados con su z-score actual (gap de spread)."""
from django.core.management.base import BaseCommand, CommandError

from engine.research.pairs import ScanConfig, find_pairs
from engine.research.report import SORT_KEYS, format_report
from pairs.management.cli import iso_date
from pairs.services import load_groups, load_price_matrix, normalize_ticker

DEFAULTS = ScanConfig()


class Command(BaseCommand):
    help = (
        'Escanea el universo y lista los pares correlacionados y cointegrados con su z-score '
        'actual. Un |z| alto es un gap de spread. Los resultados son en muestra: aún no hay '
        'validación fuera de muestra ni costes (ver docs/PLAN.md).'
    )

    def add_arguments(self, parser):
        parser.add_argument(
            'tickers', nargs='*',
            help='Universo a escanear (por defecto, todas las empresas con precios en la BD)',
        )
        parser.add_argument(
            '--group-by', choices=['sector', 'industry', 'none'], default='sector',
            help='Solo se emparejan valores del mismo grupo (por defecto: sector)',
        )
        parser.add_argument(
            '--lookback', type=int, default=DEFAULTS.lookback,
            help=f'Sesiones de la ventana, holdout incluido (por defecto {DEFAULTS.lookback}). '
                 'Con half-lives largos hace falta más historia.',
        )
        parser.add_argument(
            '--holdout', type=int, default=DEFAULTS.holdout,
            help=f'Últimas sesiones excluidas de la estimación (por defecto {DEFAULTS.holdout}); '
                 '0 = estimar con toda la ventana',
        )
        parser.add_argument('--min-corr', type=float, default=DEFAULTS.min_corr,
                            help=f'Correlación mínima de retornos (por defecto {DEFAULTS.min_corr})')
        parser.add_argument('--max-p', type=float, default=DEFAULTS.max_p_adj,
                            help=f'p-valor máximo tras la corrección FDR (por defecto {DEFAULTS.max_p_adj})')
        parser.add_argument('--min-hl', type=float, default=DEFAULTS.min_half_life,
                            help=f'Half-life mínimo en días (por defecto {DEFAULTS.min_half_life:g})')
        parser.add_argument('--max-hl', type=float, default=DEFAULTS.max_half_life,
                            help=f'Half-life máximo en días (por defecto {DEFAULTS.max_half_life:g})')
        parser.add_argument('--entry-z', type=float, default=2.0,
                            help='|z| a partir del cual se marca un gap (por defecto 2)')
        parser.add_argument('--sort', choices=sorted(SORT_KEYS), default='z',
                            help='Orden de la tabla: z (mayor gap), p, hl, corr (por defecto z)')
        parser.add_argument('--top', type=int, default=30, help='Filas a mostrar (por defecto 30)')
        parser.add_argument('--as-of', type=iso_date,
                            help='YYYY-MM-DD: ignora barras posteriores (reproduce un escaneo pasado)')

    def handle(self, *args, **options):
        if options['top'] < 1:
            raise CommandError('--top debe ser >= 1.')
        tickers = [normalize_ticker(t) for t in options['tickers']] or None
        prices = load_price_matrix(tickers, end=options['as_of'])
        if prices.empty:
            raise CommandError(
                'No hay precios en la base de datos para ese universo. Impórtalos antes, p. ej.: '
                'python manage.py import_prices AAPL MSFT'
            )
        for ticker in tickers or []:
            if ticker not in prices.columns:
                self.stderr.write(self.style.WARNING(f'Sin precios para {ticker}: se ignora.'))

        groups = self.resolve_groups(options['group_by'], list(prices.columns))

        try:
            config = ScanConfig(
                lookback=options['lookback'],
                holdout=options['holdout'],
                min_corr=options['min_corr'],
                max_p_adj=options['max_p'],
                min_half_life=options['min_hl'],
                max_half_life=options['max_hl'],
            )
            report = find_pairs(prices, groups, config)
        except ValueError as exc:
            raise CommandError(str(exc)) from None

        self.stdout.write(
            format_report(report, entry_z=options['entry_z'], sort=options['sort'], top=options['top'])
        )

    def resolve_groups(self, group_by, tickers):
        if group_by == 'none':
            return None
        groups = load_groups(tickers, group_by)
        if not groups:
            self.stderr.write(self.style.WARNING(
                f'Ningún valor tiene {group_by} en la BD: se prueban todos los pares. Se rellena con '
                '"import_prices" (perfil de FMP) o usa --group-by none para silenciar este aviso.'
            ))
            return None
        missing = sorted(set(tickers) - set(groups))
        if missing:
            self.stderr.write(self.style.WARNING(
                f'{len(missing)} valores sin {group_by} se ignoran: {", ".join(missing)}'
            ))
        return groups
