import datetime as dt

import pandas as pd
from django.test import TestCase

from engine.data.providers import Bar, CompanyProfile
from pairs.models import DailyBar
from pairs.services import (
    apply_profile,
    load_groups,
    load_price_matrix,
    normalize_ticker,
    upsert_bars,
)
from screener.models import Company


def bar(day, close, **extra):
    fields = dict(date=day, adj_open=None, adj_high=None, adj_low=None, adj_close=close, volume=None)
    fields.update(extra)
    return Bar(**fields)


class NormalizeTickerTests(TestCase):
    def test_strips_and_uppercases(self):
        self.assertEqual(normalize_ticker('  brk-b '), 'BRK-B')


class UpsertBarsTests(TestCase):
    def setUp(self):
        self.company = Company.objects.create(ticker='AAA')
        self.bars = [
            bar(dt.date(2025, 1, 2), 1.5, adj_open=1.0, adj_high=2.0, adj_low=0.5, volume=100),
            bar(dt.date(2025, 1, 3), 1.6),
        ]

    def test_inserts_bars_with_all_fields(self):
        self.assertEqual(upsert_bars(self.company, self.bars), 2)
        first = DailyBar.objects.get(company=self.company, date=dt.date(2025, 1, 2))
        self.assertEqual((first.adj_open, first.adj_high, first.adj_low, first.adj_close, first.volume),
                         (1.0, 2.0, 0.5, 1.5, 100))
        second = DailyBar.objects.get(company=self.company, date=dt.date(2025, 1, 3))
        self.assertIsNone(second.adj_open)
        self.assertIsNone(second.volume)

    def test_is_idempotent_and_updates_existing_rows(self):
        upsert_bars(self.company, self.bars)
        revised = [bar(dt.date(2025, 1, 2), 9.9, volume=7), self.bars[1]]
        self.assertEqual(upsert_bars(self.company, revised), 2)
        self.assertEqual(DailyBar.objects.count(), 2)
        first = DailyBar.objects.get(company=self.company, date=dt.date(2025, 1, 2))
        self.assertEqual((first.adj_close, first.volume), (9.9, 7))
        self.assertIsNone(first.adj_open)  # lo que el nuevo dato no trae se vacía, no se arrastra

    def test_empty_input_does_nothing(self):
        self.assertEqual(upsert_bars(self.company, []), 0)
        self.assertEqual(DailyBar.objects.count(), 0)

    def test_same_date_for_different_companies_is_allowed(self):
        other = Company.objects.create(ticker='BBB')
        upsert_bars(self.company, self.bars)
        upsert_bars(other, self.bars)
        self.assertEqual(DailyBar.objects.count(), 4)

    def test_large_batches(self):
        many = [bar(dt.date(2020, 1, 1) + dt.timedelta(days=i), 1.0 + i) for i in range(1300)]
        self.assertEqual(upsert_bars(self.company, many), 1300)
        self.assertEqual(DailyBar.objects.filter(company=self.company).count(), 1300)


class LoadPriceMatrixTests(TestCase):
    def setUp(self):
        for ticker, offset in (('AAA', 0.0), ('BBB', 100.0)):
            company = Company.objects.create(ticker=ticker)
            upsert_bars(company, [bar(dt.date(2025, 1, d), offset + d) for d in (6, 2, 3)])  # desordenadas

    def test_returns_dates_by_ticker_sorted_by_date(self):
        matrix = load_price_matrix()
        self.assertEqual(list(matrix.columns), ['AAA', 'BBB'])
        self.assertIsInstance(matrix.index, pd.DatetimeIndex)
        self.assertEqual([ts.day for ts in matrix.index], [2, 3, 6])
        self.assertEqual(matrix.loc['2025-01-06', 'BBB'], 106.0)

    def test_filters_by_tickers_and_end_date(self):
        matrix = load_price_matrix(['AAA'], end=dt.date(2025, 1, 3))
        self.assertEqual(list(matrix.columns), ['AAA'])
        self.assertEqual(len(matrix), 2)

    def test_no_data_gives_an_empty_frame(self):
        self.assertTrue(load_price_matrix(['ZZZ']).empty)
        DailyBar.objects.all().delete()
        self.assertTrue(load_price_matrix().empty)


class LoadGroupsTests(TestCase):
    def setUp(self):
        Company.objects.create(ticker='AAA', sector='Tech', industry='Software')
        Company.objects.create(ticker='BBB', sector='Tech')
        Company.objects.create(ticker='CCC', sector='', industry=None)
        Company.objects.create(ticker='DDD')

    def test_returns_only_tickers_with_the_field(self):
        self.assertEqual(load_groups(['AAA', 'BBB', 'CCC', 'DDD', 'ZZZ'], 'sector'), {'AAA': 'Tech', 'BBB': 'Tech'})
        self.assertEqual(load_groups(['AAA', 'BBB'], 'industry'), {'AAA': 'Software'})

    def test_rejects_unknown_fields(self):
        with self.assertRaises(ValueError):
            load_groups(['AAA'], 'name')


class ApplyProfileTests(TestCase):
    def profile(self, **kwargs):
        values = dict(name='Acme Inc.', exchange='NASDAQ', currency='EUR', sector='Tech',
                      industry='Software', market_cap=123)
        values.update(kwargs)
        return CompanyProfile(**values)

    def test_fills_profile_fields(self):
        company = Company.objects.create(ticker='AAA')
        changed = apply_profile(company, self.profile())
        company.refresh_from_db()
        self.assertEqual((company.sector, company.industry, company.market_cap), ('Tech', 'Software', 123))
        self.assertEqual((company.name, company.exchange), ('Acme Inc.', 'NASDAQ'))
        self.assertEqual(company.currency, 'USD')  # ya tenía valor por defecto: no se pisa
        self.assertIn('sector', changed)
        self.assertNotIn('currency', changed)

    def test_does_not_overwrite_existing_name_but_refreshes_sector_and_market_cap(self):
        company = Company.objects.create(ticker='AAA', name='Mi nombre', sector='Viejo', market_cap=1)
        apply_profile(company, self.profile())
        company.refresh_from_db()
        self.assertEqual((company.name, company.sector, company.market_cap), ('Mi nombre', 'Tech', 123))

    def test_missing_values_in_the_profile_do_not_erase_data(self):
        company = Company.objects.create(ticker='AAA', sector='Tech', market_cap=5)
        changed = apply_profile(company, self.profile(sector=None, market_cap=None, industry=None,
                                                      name=None, exchange=None))
        company.refresh_from_db()
        self.assertEqual((company.sector, company.market_cap), ('Tech', 5))
        self.assertEqual(changed, [])
