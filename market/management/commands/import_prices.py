"""Importa precios diarios ajustados desde la línea de comandos."""
import argparse
import datetime as dt

from django.core.management.base import BaseCommand, CommandError

from market.importers import import_csv_dir, import_prices
from market.models import Universe
from market.services import load_presets, normalize_ticker


def iso_date(value):
    try:
        return dt.date.fromisoformat(value)
    except ValueError:
        raise argparse.ArgumentTypeError(f'fecha inválida "{value}": usa YYYY-MM-DD') from None


class Command(BaseCommand):
    help = 'Importa precios diarios ajustados (Yahoo por defecto, FMP o CSV) a la base de datos. Es idempotente.'

    def add_arguments(self, parser):
        parser.add_argument('tickers', nargs='*', help='Tickers, p. ej. AAPL MSFT')
        parser.add_argument('--universe', help='Nombre de un universo: importa sus valores y su benchmark')
        parser.add_argument('--presets', action='store_true', help='Crea los universos predefinidos e importa todos sus valores')
        parser.add_argument('--provider', choices=['yahoo', 'fmp'], default='yahoo')
        parser.add_argument('--csv-dir', help='Carpeta con TICKER.csv: lee de ahí en lugar de un proveedor')
        parser.add_argument('--from', dest='start', type=iso_date, default=dt.date(2010, 1, 1), help='Desde (por defecto 2010-01-01)')
        parser.add_argument('--to', dest='end', type=iso_date, help='Hasta (por defecto, hoy)')

    def handle(self, *args, **options):
        tickers = [normalize_ticker(t) for t in options['tickers']]
        universes = []
        if options['presets']:
            universes += [u for u in load_presets()]
        if options['universe']:
            try:
                universes.append(Universe.objects.get(name=options['universe']))
            except Universe.DoesNotExist:
                raise CommandError(f'No existe el universo «{options["universe"]}».') from None
        for universe in universes:
            tickers += universe.tickers() + ([universe.benchmark] if universe.benchmark else [])
        tickers = list(dict.fromkeys(tickers))
        if not tickers:
            raise CommandError('Indica tickers, --universe o --presets.')
        if options['end'] and options['end'] < options['start']:
            raise CommandError('--to debe ser posterior a --from.')

        def progress(fraction, message):
            self.stdout.write(f'  {100 * fraction:5.1f} %  {message}')

        if options['csv_dir']:
            summary = import_csv_dir(tickers, options['csv_dir'], options['start'], options['end'])
        else:
            self.stdout.write(f'Importando {len(tickers)} valores desde {options["provider"]}...')
            summary = import_prices(tickers, options['provider'], options['start'], options['end'], progress=progress)
        for ticker, error in summary['errors'].items():
            self.stderr.write(self.style.WARNING(f'{ticker}: {error}'))
        self.stdout.write(summary['message'])
        if summary['errors'] and not summary['imported']:
            raise CommandError('No se importó ningún valor.')
