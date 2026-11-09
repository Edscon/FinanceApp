import datetime as dt
import tempfile
from io import StringIO
from pathlib import Path
from unittest import mock

from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import TestCase, override_settings

from engine.data.providers import Bar, CompanyProfile, ProviderError
from engine.data.synthetic import random_walk
from pairs.models import DailyBar
from pairs.tests.helpers import seed_synthetic_universe
from screener.models import Company

IMPORT = 'pairs.management.commands.import_prices'


def run(command, *args):
    out, err = StringIO(), StringIO()
    call_command(command, *args, stdout=out, stderr=err)
    return out.getvalue(), err.getvalue()


class FindPairsCommandTests(TestCase):
    def test_lists_the_cointegrated_pairs_and_flags_the_gap(self):
        seed_synthetic_universe(jump=('TA', 0.20))  # TA da un salto del +20 % en la última sesión
        out, err = run('find_pairs')
        self.assertIn('PAR (y / x)', out)
        self.assertIn('CORTO TA / LARGO TB', out)  # el gap: TA caro frente a TB
        self.assertIn('EA', out)  # el otro par cointegrado
        self.assertNotIn('TC / TD', out)  # correlacionados pero sin cointegración: no sale
        self.assertNotIn('TD / TC', out)
        self.assertIn('Aviso: resultados en muestra', out)
        self.assertEqual(err, '')

    def test_without_sectors_it_warns_and_tests_every_pair(self):
        seed_synthetic_universe(with_sector=False)
        out, err = run('find_pairs')
        self.assertIn('Ningún valor tiene sector', err)
        # Sin grupos se consideran todos los pares, pero el filtro de correlación deja los mismos 3.
        self.assertIn('3 pares probados', out)
        self.assertIn('TA', out)

    def test_group_by_none_is_silent(self):
        seed_synthetic_universe(with_sector=False)
        _, err = run('find_pairs', '--group-by', 'none')
        self.assertEqual(err, '')

    def test_tickers_without_group_are_reported(self):
        seed_synthetic_universe()
        Company.objects.filter(ticker='TD').update(sector=None)
        _, err = run('find_pairs')
        self.assertIn('1 valores sin sector se ignoran: TD', err)

    def test_tickers_argument_restricts_the_universe(self):
        seed_synthetic_universe()
        out, err = run('find_pairs', 'ta', 'TB', 'ZZZ')
        self.assertIn('2 tickers', out)
        self.assertIn('Sin precios para ZZZ', err)
        self.assertNotIn('EA', out)

    def test_as_of_replays_a_past_scan(self):
        seed_synthetic_universe()
        out, _ = run('find_pairs', '--as-of', '2025-06-30')
        self.assertIn('Escaneo a 2025-06-30', out)

    def test_entry_threshold_and_top_options_reach_the_report(self):
        seed_synthetic_universe(jump=('TA', 0.20))
        out, _ = run('find_pairs', '--entry-z', '50')
        self.assertNotIn('CORTO', out)
        out, _ = run('find_pairs', '--top', '1')
        self.assertIn('... y 1 más', out)

    def test_sort_option_is_accepted(self):
        seed_synthetic_universe()
        for sort in ('z', 'p', 'hl', 'corr'):
            run('find_pairs', '--sort', sort)

    def test_empty_database_gives_a_helpful_error(self):
        with self.assertRaisesMessage(CommandError, 'No hay precios en la base de datos'):
            run('find_pairs')

    def test_top_must_be_positive(self):
        with self.assertRaisesMessage(CommandError, '--top'):
            run('find_pairs', '--top', '0')

    def test_invalid_parameters_become_command_errors(self):
        seed_synthetic_universe()
        with self.assertRaisesMessage(CommandError, 'lookback'):
            run('find_pairs', '--lookback', '50')
        with self.assertRaisesMessage(CommandError, 'holdout'):
            run('find_pairs', '--holdout', '25')

    def test_too_little_history_becomes_a_command_error(self):
        seed_synthetic_universe(n=60)
        with self.assertRaisesMessage(CommandError, 'sesiones'):
            run('find_pairs')


def write_csv(directory, ticker, series):
    path = Path(directory) / f'{ticker}.csv'
    series.rename('adj_close').rename_axis('date').to_csv(path, date_format='%Y-%m-%d')
    return path


class ImportPricesFromCsvTests(TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.dir = self.tmp.name
        for ticker, seed in (('AAA', 1), ('BBB', 2)):
            write_csv(self.dir, ticker, random_walk(120, seed=seed, name=ticker))

    def test_imports_and_creates_companies(self):
        out, err = run('import_prices', 'aaa', 'BBB', '--csv-dir', self.dir)
        self.assertEqual(sorted(Company.objects.values_list('ticker', flat=True)), ['AAA', 'BBB'])
        self.assertEqual(DailyBar.objects.filter(company__ticker='AAA').count(), 120)
        self.assertIn('AAA: 120 barras', out)
        self.assertIn('Importados 2 de 2 tickers.', out)
        self.assertEqual(err, '')

    def test_rerunning_is_idempotent(self):
        run('import_prices', 'AAA', '--csv-dir', self.dir)
        run('import_prices', 'AAA', '--csv-dir', self.dir)
        self.assertEqual(DailyBar.objects.count(), 120)
        self.assertEqual(Company.objects.count(), 1)

    def test_from_and_to_filter_the_rows(self):
        run('import_prices', 'AAA', '--csv-dir', self.dir, '--from', '2025-12-01', '--to', '2025-12-31')
        dates = list(DailyBar.objects.values_list('date', flat=True))
        self.assertEqual(len(dates), 23)  # días laborables de diciembre de 2025
        self.assertTrue(all(dt.date(2025, 12, 1) <= d <= dt.date(2025, 12, 31) for d in dates))

    def test_a_missing_file_fails_at_the_end_but_the_rest_is_imported(self):
        with self.assertRaises(CommandError) as ctx:
            run('import_prices', 'AAA', 'NOPE', 'BBB', '--csv-dir', self.dir)
        self.assertIn('NOPE', str(ctx.exception))
        self.assertEqual(sorted(Company.objects.values_list('ticker', flat=True)), ['AAA', 'BBB'])

    def test_all_uses_the_companies_already_in_the_database(self):
        Company.objects.create(ticker='AAA')
        out, _ = run('import_prices', '--all', '--csv-dir', self.dir)
        self.assertIn('Importados 1 de 1 tickers.', out)

    def test_argument_errors(self):
        for args, message in (
            ((), 'Indica al menos un ticker'),
            (('--all', 'AAA'), 'no ambos'),
            (('AAA', '--csv-dir', self.dir + '/no-existe'), 'no es una carpeta'),
            (('AAA', '--csv-dir', self.dir, '--from', '2025-02-01', '--to', '2025-01-01'), 'anterior'),
            (('AAA', '--csv-dir', self.dir, '--from', 'ayer'), 'fecha inválida'),
        ):
            with self.subTest(args=args), self.assertRaisesMessage(CommandError, message):
                run('import_prices', *args)


class FakeFMP:
    """Sustituto de FMPProvider: sin red, con casos de error según el ticker."""

    instances = []

    def __init__(self, api_key):
        self.api_key = api_key
        self.bar_calls = []
        self.profile_calls = []
        FakeFMP.instances.append(self)

    def daily_bars(self, ticker, start, end):
        self.bar_calls.append((ticker, start, end))
        if ticker == 'BAD':
            raise ProviderError('HTTP 404')
        if ticker == 'NONE':
            return []
        return [
            Bar(dt.date(2025, 1, 2), 1.0, 2.0, 0.5, 1.5, 100),
            Bar(dt.date(2025, 1, 3), 1.1, 2.1, 0.6, 1.6, 200),
        ]

    def profile(self, ticker):
        self.profile_calls.append(ticker)
        if ticker == 'NOPROFILE':
            raise ProviderError('HTTP 403')
        return CompanyProfile('Acme Inc.', 'NASDAQ', 'USD', 'Technology', 'Software', 123)


@override_settings(FMP_API_KEY='fake-key')
class ImportPricesFromFmpTests(TestCase):
    def setUp(self):
        FakeFMP.instances = []
        patcher = mock.patch(f'{IMPORT}.FMPProvider', FakeFMP)
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_imports_prices_and_profile(self):
        out, err = run('import_prices', 'AAA', '--sleep', '0')
        company = Company.objects.get(ticker='AAA')
        self.assertEqual((company.sector, company.industry, company.market_cap), ('Technology', 'Software', 123))
        self.assertEqual(company.name, 'Acme Inc.')
        self.assertEqual(DailyBar.objects.filter(company=company).count(), 2)
        self.assertIn('AAA: 2 barras (2025-01-02 a 2025-01-03)', out)
        self.assertEqual(err, '')
        self.assertEqual(FakeFMP.instances[0].api_key, 'fake-key')

    def test_default_start_is_five_years_back(self):
        run('import_prices', 'AAA', '--sleep', '0')
        _, start, end = FakeFMP.instances[0].bar_calls[0]
        self.assertAlmostEqual((dt.date.today() - start).days, 365 * 5, delta=1)
        self.assertIsNone(end)

    def test_explicit_dates_are_passed_through(self):
        run('import_prices', 'AAA', '--from', '2024-01-02', '--to', '2024-02-03', '--sleep', '0')
        self.assertEqual(FakeFMP.instances[0].bar_calls[0], ('AAA', dt.date(2024, 1, 2), dt.date(2024, 2, 3)))

    def test_no_profile_skips_the_profile_request(self):
        run('import_prices', 'AAA', '--no-profile', '--sleep', '0')
        self.assertEqual(FakeFMP.instances[0].profile_calls, [])
        self.assertIsNone(Company.objects.get(ticker='AAA').sector)

    def test_profile_is_not_requested_again_when_the_sector_is_known(self):
        Company.objects.create(ticker='AAA', sector='Energy')
        run('import_prices', 'AAA', '--sleep', '0')
        self.assertEqual(FakeFMP.instances[0].profile_calls, [])
        self.assertEqual(Company.objects.get(ticker='AAA').sector, 'Energy')

    def test_a_profile_failure_only_warns_and_keeps_the_prices(self):
        out, err = run('import_prices', 'NOPROFILE', '--sleep', '0')
        self.assertEqual(DailyBar.objects.count(), 2)
        self.assertIn('perfil no disponible', err)
        self.assertIn('Importados 1 de 1 tickers.', out)

    def test_failures_do_not_stop_the_other_tickers_and_set_a_failing_exit(self):
        with self.assertRaises(CommandError) as ctx:
            run('import_prices', 'AAA', 'BAD', 'NONE', 'BBB', '--sleep', '0')
        self.assertIn('Fallaron 2 tickers: BAD, NONE', str(ctx.exception))
        self.assertEqual(sorted(Company.objects.values_list('ticker', flat=True)), ['AAA', 'BBB'])

    def test_no_company_is_created_for_a_ticker_without_data(self):
        with self.assertRaises(CommandError):
            run('import_prices', 'NONE', '--sleep', '0')
        self.assertFalse(Company.objects.filter(ticker='NONE').exists())


class ImportPricesWithoutApiKeyTests(TestCase):
    @override_settings(FMP_API_KEY=None)
    def test_missing_key_is_a_clear_error(self):
        with self.assertRaisesMessage(CommandError, 'FMP_API_KEY'):
            run('import_prices', 'AAA')
