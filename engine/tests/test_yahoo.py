import datetime as dt
import unittest
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd

from engine.data.providers import ProviderError, YahooProvider, parse_yf_frame

NY = ZoneInfo('America/New_York')


def frame(tickers, price_first=True, tz=None, rows=3):
    index = pd.date_range('2024-01-02', periods=rows, freq='B', tz=tz)
    fields = ['Close', 'High', 'Low', 'Open', 'Volume']
    data = {}
    for k, ticker in enumerate(tickers):
        base = 100.0 * (k + 1)
        for field in fields:
            values = np.arange(rows, dtype=float) + base + (0 if field != 'Volume' else 1e6)
            key = (field, ticker) if price_first else (ticker, field)
            data[key] = values
    return pd.DataFrame(data, index=index)


class ParseFrameTests(unittest.TestCase):
    def test_price_first_multiindex(self):
        bars = parse_yf_frame(frame(['AAA', 'BBB']), ['AAA', 'BBB'])
        self.assertEqual(len(bars['AAA']), 3)
        first = bars['BBB'][0]
        self.assertEqual((first.date, first.adj_close, first.volume), (dt.date(2024, 1, 2), 200.0, 1000200))

    def test_ticker_first_multiindex(self):
        bars = parse_yf_frame(frame(['AAA', 'BBB'], price_first=False), ['AAA', 'BBB'])
        self.assertEqual(bars['AAA'][-1].adj_close, 102.0)

    def test_flat_columns_single_ticker(self):
        flat = frame(['AAA']).xs('AAA', axis=1, level=1)
        self.assertEqual(len(parse_yf_frame(flat, ['AAA'])['AAA']), 3)
        with self.assertRaises(ProviderError):
            parse_yf_frame(flat, ['AAA', 'BBB'])

    def test_rows_without_close_are_dropped(self):
        data = frame(['AAA', 'BBB'])
        data.loc[data.index[0], ('Close', 'BBB')] = np.nan
        bars = parse_yf_frame(data, ['AAA', 'BBB'])
        self.assertEqual([b.date for b in bars['BBB']], [dt.date(2024, 1, 3), dt.date(2024, 1, 4)])

    def test_timezone_aware_index_keeps_the_exchange_date(self):
        bars = parse_yf_frame(frame(['AAA'], tz='Asia/Tokyo'), ['AAA'])
        self.assertEqual(bars['AAA'][0].date, dt.date(2024, 1, 2))

    def test_empty_and_missing(self):
        self.assertEqual(parse_yf_frame(None, ['AAA']), {})
        self.assertEqual(parse_yf_frame(pd.DataFrame(), ['AAA']), {})
        self.assertNotIn('ZZZ', parse_yf_frame(frame(['AAA']), ['AAA', 'ZZZ']))


class FakeDownload:
    def __init__(self, failures=0):
        self.calls = []
        self.failures = failures

    def __call__(self, tickers, **kwargs):
        self.calls.append((list(tickers), kwargs))
        if self.failures:
            self.failures -= 1
            raise RuntimeError('rate limited')
        return frame([t for t in tickers if t != 'NOPE'])


def provider(download, now=dt.datetime(2024, 2, 1, 18, 0, tzinfo=NY), **kw):
    sleeps = []
    return YahooProvider(download=download, sleep=sleeps.append, now=lambda: now, **kw), sleeps


class YahooProviderTests(unittest.TestCase):
    def test_chunks_and_inclusive_end(self):
        download = FakeDownload()
        yahoo, sleeps = provider(download, chunk_size=2, pause=1.5)
        bars, errors = yahoo.daily_bars_many(['AAA', 'BBB', 'CCC'], dt.date(2024, 1, 1), dt.date(2024, 1, 31))
        self.assertEqual([c[0] for c in download.calls], [['AAA', 'BBB'], ['CCC']])
        kwargs = download.calls[0][1]
        self.assertEqual((kwargs['start'], kwargs['end'], kwargs['auto_adjust']), ('2024-01-01', '2024-02-01', True))
        self.assertEqual(set(bars), {'AAA', 'BBB', 'CCC'})
        self.assertEqual(errors, {})
        self.assertEqual(sleeps, [1.5])

    def test_retries_then_succeeds(self):
        yahoo, sleeps = provider(FakeDownload(failures=2), backoff=1.0)
        bars, errors = yahoo.daily_bars_many(['AAA'])
        self.assertIn('AAA', bars)
        self.assertEqual(sleeps, [1.0, 2.0])

    def test_gives_up_and_reports_each_ticker(self):
        yahoo, _ = provider(FakeDownload(failures=10), max_retries=1)
        bars, errors = yahoo.daily_bars_many(['AAA', 'BBB'])
        self.assertEqual(bars, {})
        self.assertIn('RuntimeError', errors['AAA'])

    def test_unknown_ticker_is_an_error_not_a_crash(self):
        yahoo, _ = provider(FakeDownload())
        bars, errors = yahoo.daily_bars_many(['AAA', 'NOPE'])
        self.assertIn('AAA', bars)
        self.assertIn('NOPE', errors)
        with self.assertRaises(ProviderError):
            yahoo.daily_bars('NOPE')

    def test_partial_bar_of_today_is_dropped_while_the_market_is_open(self):
        today_open = dt.datetime(2024, 1, 4, 11, 0, tzinfo=NY)  # 2024-01-04 es la última fila
        yahoo, _ = provider(FakeDownload(), now=today_open)
        self.assertEqual(yahoo.daily_bars('AAA')[-1].date, dt.date(2024, 1, 3))
        after_close = dt.datetime(2024, 1, 4, 17, 0, tzinfo=NY)
        yahoo, _ = provider(FakeDownload(), now=after_close)
        self.assertEqual(yahoo.daily_bars('AAA')[-1].date, dt.date(2024, 1, 4))


if __name__ == '__main__':
    unittest.main()
