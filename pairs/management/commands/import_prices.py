"""Importa barras diarias ajustadas a la base de datos, desde FMP o desde CSV."""
import datetime as dt
import time
from pathlib import Path

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from engine.data.providers import FMPProvider, ProviderError, read_price_csv
from pairs.management.cli import iso_date
from pairs.services import apply_profile, normalize_ticker, upsert_bars
from screener.models import Company

DEFAULT_YEARS = 5


class Command(BaseCommand):
    help = (
        'Importa barras diarias ajustadas (FMP o CSV) a la base de datos. Es idempotente: '
        'volver a ejecutarlo actualiza las barras existentes.'
    )

    def add_arguments(self, parser):
        parser.add_argument('tickers', nargs='*', help='Símbolos, p. ej. AAPL MSFT')
        parser.add_argument('--all', action='store_true', help='Todas las empresas que ya están en la BD')
        parser.add_argument(
            '--from', dest='start', type=iso_date,
            help=f'Fecha inicial YYYY-MM-DD (FMP: por defecto hace {DEFAULT_YEARS} años)',
        )
        parser.add_argument('--to', dest='end', type=iso_date, help='Fecha final YYYY-MM-DD')
        parser.add_argument(
            '--csv-dir',
            help='Carpeta con un TICKER.csv por valor (date + adj_close o close): lee de ahí en lugar de FMP',
        )
        parser.add_argument(
            '--no-profile', action='store_true',
            help='No pedir a FMP el sector/industria de las empresas que no lo tengan',
        )
        parser.add_argument(
            '--sleep', type=float, default=0.25,
            help='Pausa en segundos entre tickers al usar FMP (por defecto 0.25)',
        )

    def handle(self, *args, **options):
        tickers = self.resolve_tickers(options)
        csv_dir = Path(options['csv_dir']) if options['csv_dir'] else None
        start, end = options['start'], options['end']
        if csv_dir is None and start is None:
            start = dt.date.today() - dt.timedelta(days=365 * DEFAULT_YEARS)
        if start and end and start > end:
            raise CommandError('--from debe ser anterior o igual a --to.')

        fmp = None
        if csv_dir is not None:
            if not csv_dir.is_dir():
                raise CommandError(f'--csv-dir no es una carpeta: {csv_dir}')
        else:
            try:
                fmp = FMPProvider(settings.FMP_API_KEY)
            except ProviderError as exc:
                raise CommandError(str(exc)) from None

        failures = {}
        for position, ticker in enumerate(tickers):
            try:
                self.import_one(ticker, fmp, csv_dir, start, end, fetch_profile=not options['no_profile'])
            except ProviderError as exc:
                failures[ticker] = str(exc)
                self.stderr.write(self.style.ERROR(f'{ticker}: {exc}'))
            if fmp is not None and options['sleep'] > 0 and position < len(tickers) - 1:
                time.sleep(options['sleep'])

        self.stdout.write(f'Importados {len(tickers) - len(failures)} de {len(tickers)} tickers.')
        if failures:
            raise CommandError(f'Fallaron {len(failures)} tickers: {", ".join(failures)}')

    def resolve_tickers(self, options):
        if options['all'] and options['tickers']:
            raise CommandError('Usa tickers o --all, no ambos.')
        if options['all']:
            tickers = list(Company.objects.order_by('ticker').values_list('ticker', flat=True))
        else:
            tickers = [normalize_ticker(t) for t in options['tickers']]
        tickers = list(dict.fromkeys(t for t in tickers if t))  # sin duplicados, en orden
        if not tickers:
            raise CommandError('Indica al menos un ticker o usa --all.')
        return tickers

    def import_one(self, ticker, fmp, csv_dir, start, end, fetch_profile):
        if fmp is not None:
            bars = fmp.daily_bars(ticker, start, end)
        else:
            bars = [
                bar for bar in read_price_csv(csv_dir / f'{ticker}.csv')
                if (start is None or bar.date >= start) and (end is None or bar.date <= end)
            ]
        if not bars:
            raise ProviderError('sin datos en el rango pedido')
        with transaction.atomic():
            company, _ = Company.objects.get_or_create(ticker=ticker)
            count = upsert_bars(company, bars)
        self.stdout.write(f'{ticker}: {count} barras ({bars[0].date} a {bars[-1].date})')
        if fmp is not None and fetch_profile and not company.sector:
            self.fill_profile(fmp, company)

    def fill_profile(self, fmp, company):
        try:
            profile = fmp.profile(company.ticker)
        except ProviderError as exc:
            self.stderr.write(self.style.WARNING(f'{company.ticker}: perfil no disponible ({exc})'))
            return
        if profile is None:
            self.stderr.write(self.style.WARNING(f'{company.ticker}: FMP no devolvió perfil'))
            return
        apply_profile(company, profile)
