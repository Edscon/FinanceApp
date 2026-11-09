import datetime as dt
import tempfile
from pathlib import Path
from unittest import mock

import numpy as np
import pandas as pd
from django.core.management import call_command
from django.test import TestCase, override_settings
from django.urls import reverse

from engine.data.providers import Bar
from market.importers import DEMO_UNIVERSE, import_prices, load_demo
from market.models import DailyBar, Instrument, Universe
from market.services import load_market_data, load_presets, parse_members, parse_tickers, upsert_bars


def bars(n=5, start=dt.date(2024, 1, 1), price=10.0):
    return [Bar(start + dt.timedelta(days=i), price, price, price, price + i, 1000) for i in range(n)]


class FakeYahoo:
    def __init__(self, *args, **kwargs):
        pass

    def daily_bars_many(self, tickers, start=None, end=None, progress=None):
        found = {t: bars() for t in tickers if t != 'NOPE'}
        errors = {t: 'Yahoo no devolvió datos' for t in tickers if t == 'NOPE'}
        if progress:
            progress(1.0, 'ok')
        return found, errors


class ParsingTests(TestCase):
    def test_parse_tickers(self):
        self.assertEqual(parse_tickers('aapl, msft  AAPL\nspy'), ['AAPL', 'MSFT', 'SPY'])

    def test_parse_members(self):
        rows = parse_members('KO, Bebidas\n# comentario\npep; Bebidas\nXOM\nKO, Otro')
        self.assertEqual(rows, [('KO', 'Bebidas'), ('PEP', 'Bebidas'), ('XOM', '')])


class ServiceTests(TestCase):
    def test_upsert_is_idempotent_and_refreshes_stats(self):
        instrument = Instrument.objects.create(ticker='AAA')
        upsert_bars(instrument, bars(), source='yahoo')
        upsert_bars(instrument, bars(), source='yahoo')
        instrument.refresh_from_db()
        self.assertEqual(DailyBar.objects.count(), 5)
        self.assertEqual((instrument.n_bars, instrument.first_date, instrument.source), (5, dt.date(2024, 1, 1), 'yahoo'))
        self.assertEqual(instrument.quality, [])

    def test_quality_flags(self):
        instrument = Instrument.objects.create(ticker='BAD')
        data = bars(3) + [Bar(dt.date(2024, 2, 1), None, None, None, 30.0, None)]
        upsert_bars(instrument, data)
        instrument.refresh_from_db()
        self.assertTrue(any('huecos' in f for f in instrument.quality))
        self.assertTrue(any('extremos' in f for f in instrument.quality))

    def test_presets_are_idempotent(self):
        load_presets()
        load_presets()
        stocks = Universe.objects.get(name='US grandes valores')
        self.assertGreater(stocks.members.count(), 100)
        self.assertEqual(stocks.groups()['JPM'], 'Financiero')
        self.assertEqual(Universe.objects.filter(is_preset=True).count(), 2)
        self.assertTrue(Instrument.objects.filter(ticker='SPY').exists())

    def test_demo_and_market_data(self):
        universe = load_demo(n=300)
        load_demo(n=300)
        self.assertEqual(universe.name, DEMO_UNIVERSE)
        self.assertEqual(universe.members.count(), 26)
        data = load_market_data(universe)
        self.assertEqual(data.close.shape, (300, 26))
        self.assertIsNotNone(data.open)
        self.assertEqual(data.groups['DEMO-RA1'], 'Reversión A')
        end = data.dates[100].date()
        self.assertEqual(len(load_market_data(universe, end=end).dates), 101)

    def test_market_data_requires_prices(self):
        universe = Universe.objects.create(name='Vacío')
        with self.assertRaisesMessage(ValueError, 'no tiene precios'):
            load_market_data(universe)

    def test_benchmark_is_loaded(self):
        universe = load_demo(n=200)
        spy = Instrument.objects.create(ticker='SPY')
        upsert_bars(spy, [Bar(d.date(), None, None, None, 100.0 + i, None) for i, d in enumerate(pd.bdate_range(end='2025-12-31', periods=200))])
        universe.benchmark = 'SPY'
        universe.save()
        data = load_market_data(universe)
        self.assertEqual(data.benchmark_name, 'SPY')
        self.assertTrue(np.isfinite(data.benchmark.to_numpy()).all())

    @mock.patch('market.importers.YahooProvider', FakeYahoo)
    def test_import_from_yahoo(self):
        summary = import_prices(['aaa', 'NOPE'], 'yahoo', dt.date(2024, 1, 1))
        self.assertEqual(list(summary['imported']), ['AAA'])
        self.assertIn('NOPE', summary['errors'])
        self.assertEqual(Instrument.objects.get(ticker='AAA').source, 'yahoo')

    @override_settings(FMP_API_KEY=None)
    def test_fmp_without_key_reports_every_ticker(self):
        summary = import_prices(['AAA'], 'fmp')
        self.assertIn('FMP_API_KEY', summary['errors']['AAA'])


@override_settings(JOBS_MODE='sync')
@mock.patch('market.importers.YahooProvider', FakeYahoo)
class ViewTests(TestCase):
    def test_data_page_and_import(self):
        self.assertEqual(self.client.get(reverse('market:data')).status_code, 200)
        response = self.client.post(reverse('market:data'), {'provider': 'yahoo', 'tickers': 'AAA BBB', 'start': '2024-01-01'})
        self.assertRedirects(response, reverse('market:data'))
        self.assertEqual(Instrument.objects.filter(n_bars__gt=0).count(), 2)
        response = self.client.get(reverse('market:data'))
        self.assertContains(response, 'AAA')

    def test_import_form_requires_something_to_import(self):
        response = self.client.post(reverse('market:data'), {'provider': 'yahoo', 'start': '2024-01-01'})
        self.assertContains(response, 'Elige un universo')

    def test_demo_presets_and_universe_pages(self):
        self.client.post(reverse('market:demo'))
        demo = Universe.objects.get(name=DEMO_UNIVERSE)
        self.client.post(reverse('market:presets'))
        for url in (reverse('market:universes'), reverse('market:universe', args=[demo.pk]),
                    reverse('market:universe_new'), reverse('market:instrument', args=['DEMO-RA1'])):
            self.assertEqual(self.client.get(url).status_code, 200, url)

    def test_create_edit_import_and_delete_a_universe(self):
        response = self.client.post(reverse('market:universe_new'), {
            'name': 'Bebidas', 'description': '', 'benchmark': 'SPY', 'members': 'KO, Bebidas\nPEP, Bebidas\nKDP, Bebidas'})
        universe = Universe.objects.get(name='Bebidas')
        self.assertRedirects(response, reverse('market:universe', args=[universe.pk]))
        self.assertEqual(universe.tickers(), ['KDP', 'KO', 'PEP'])
        self.client.post(reverse('market:universe', args=[universe.pk]), {
            'name': 'Bebidas', 'benchmark': 'SPY', 'members': 'KO, Bebidas\nPEP, Bebidas\nMNST, Bebidas'})
        self.assertEqual(universe.tickers(), ['KO', 'MNST', 'PEP'])
        self.client.post(reverse('market:universe_import', args=[universe.pk]))
        self.assertTrue(Instrument.objects.get(ticker='KO').n_bars)
        self.client.post(reverse('market:universe_delete', args=[universe.pk]))
        self.assertFalse(Universe.objects.filter(name='Bebidas').exists())

    def test_universe_needs_three_members(self):
        response = self.client.post(reverse('market:universe_new'), {'name': 'X', 'members': 'KO\nPEP'})
        self.assertContains(response, 'al menos 3')


class CommandTests(TestCase):
    def test_load_demo_and_presets(self):
        call_command('load_demo', stdout=mock.MagicMock())
        call_command('load_presets', stdout=mock.MagicMock())
        self.assertEqual(Universe.objects.count(), 3)

    def test_import_from_csv(self):
        with tempfile.TemporaryDirectory() as tmp:
            Path(tmp, 'AAA.csv').write_text('date,close\n2024-01-02,10\n2024-01-03,11\n', encoding='utf-8')
            call_command('import_prices', 'AAA', csv_dir=tmp, stdout=mock.MagicMock(), stderr=mock.MagicMock())
        self.assertEqual(Instrument.objects.get(ticker='AAA').n_bars, 2)
