import datetime as dt
import tempfile
import unittest
from pathlib import Path

import requests

from engine.data.providers import (
    FMPProvider,
    ProviderError,
    parse_eod_records,
    parse_profile,
    read_price_csv,
)

SECRET = 'SECRET123'

# FMP suele devolver el más reciente primero.
ADJ_RECORDS = [
    {'symbol': 'AAPL', 'date': '2025-01-03', 'adjOpen': 243.0, 'adjHigh': 244.2,
     'adjLow': 241.9, 'adjClose': 243.36, 'volume': 40244100},
    {'symbol': 'AAPL', 'date': '2025-01-02', 'adjOpen': 248.9, 'adjHigh': 249.1,
     'adjLow': 241.8, 'adjClose': 243.85, 'volume': 55740700},
]
PLAIN_RECORDS = [
    {'date': '2025-01-03', 'open': 243.0, 'high': 244.2, 'low': 241.9, 'close': 243.36, 'volume': 40244100},
    {'date': '2025-01-02', 'open': 248.9, 'high': 249.1, 'low': 241.8, 'close': 243.85, 'volume': 55740700},
]


class FakeResponse:
    def __init__(self, status=200, payload=None, text=''):
        self.status_code = status
        self._payload = payload
        self.text = text

    def json(self):
        if self._payload is None:
            raise ValueError('sin JSON')
        return self._payload


class FakeSession:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []

    def get(self, url, params=None, timeout=None):
        self.calls.append((url, dict(params or {}), timeout))
        item = self.responses.pop(0)
        if isinstance(item, Exception):
            raise item
        return item


def provider(responses, **kwargs):
    session = FakeSession(responses)
    sleeps = []
    kwargs.setdefault('max_retries', 3)
    return FMPProvider(SECRET, session=session, sleep=sleeps.append, backoff=1.0, **kwargs), session, sleeps


class ParseEodTests(unittest.TestCase):
    def test_adj_keys_are_parsed_and_sorted_oldest_first(self):
        bars = parse_eod_records(ADJ_RECORDS)
        self.assertEqual([b.date for b in bars], [dt.date(2025, 1, 2), dt.date(2025, 1, 3)])
        first = bars[0]
        self.assertEqual(
            (first.adj_open, first.adj_high, first.adj_low, first.adj_close, first.volume),
            (248.9, 249.1, 241.8, 243.85, 55740700),
        )

    def test_plain_keys_give_the_same_result(self):
        self.assertEqual(parse_eod_records(PLAIN_RECORDS), parse_eod_records(ADJ_RECORDS))

    def test_adj_close_wins_over_close_when_both_exist(self):
        bars = parse_eod_records([{'date': '2025-01-02', 'close': 100.0, 'adjClose': 90.0}])
        self.assertEqual(bars[0].adj_close, 90.0)

    def test_legacy_wrapper_is_accepted(self):
        self.assertEqual(len(parse_eod_records({'symbol': 'AAPL', 'historical': PLAIN_RECORDS})), 2)

    def test_invalid_records_are_skipped_and_reported(self):
        with self.assertLogs('engine.data.providers', level='WARNING') as logs:
            bars = parse_eod_records([
                {'date': 'no-es-fecha', 'close': 10},
                {'date': '2025-01-02', 'close': 0},
                {'date': '2025-01-03', 'close': None},
                {'date': '2025-01-06'},
                'basura',
                {'date': '2025-01-07', 'close': 12.5},
            ])
        self.assertEqual([b.date for b in bars], [dt.date(2025, 1, 7)])
        self.assertIn('5 registros inválidos de 6', logs.output[0])

    def test_optional_fields_may_be_missing(self):
        bar = parse_eod_records([{'date': '2025-01-02', 'close': 10}])[0]
        self.assertEqual((bar.adj_open, bar.adj_high, bar.adj_low, bar.volume), (None, None, None, None))

    def test_duplicate_dates_keep_the_last_record(self):
        bars = parse_eod_records([{'date': '2025-01-02', 'close': 10}, {'date': '2025-01-02', 'close': 11}])
        self.assertEqual([b.adj_close for b in bars], [11.0])

    def test_unexpected_payloads_raise(self):
        for payload in ({'foo': 1}, 'texto', 42, None):
            with self.subTest(payload=payload), self.assertRaises(ProviderError):
                parse_eod_records(payload)

    def test_empty_list_is_valid(self):
        self.assertEqual(parse_eod_records([]), [])


class ParseProfileTests(unittest.TestCase):
    def test_parses_the_first_record(self):
        profile = parse_profile([{
            'companyName': 'Apple Inc.', 'exchange': 'NASDAQ', 'currency': 'USD',
            'sector': 'Technology', 'industry': 'Consumer Electronics', 'marketCap': 3882897839444.9995,
        }])
        self.assertEqual(
            (profile.name, profile.exchange, profile.currency, profile.sector, profile.industry, profile.market_cap),
            ('Apple Inc.', 'NASDAQ', 'USD', 'Technology', 'Consumer Electronics', 3882897839444),
        )

    def test_legacy_market_cap_key_and_blank_values(self):
        profile = parse_profile([{'mktCap': 1000, 'sector': '  ', 'industry': ''}])
        self.assertEqual((profile.market_cap, profile.sector, profile.industry), (1000, None, None))

    def test_empty_or_invalid_payload_gives_none(self):
        for payload in ([], None, 'x', [1]):
            with self.subTest(payload=payload):
                self.assertIsNone(parse_profile(payload))


class FMPProviderTests(unittest.TestCase):
    def test_builds_the_request_and_parses_the_answer(self):
        fmp, session, _ = provider([FakeResponse(200, ADJ_RECORDS)])
        bars = fmp.daily_bars('AAPL', dt.date(2025, 1, 2), dt.date(2025, 1, 10))
        url, params, timeout = session.calls[0]
        self.assertTrue(url.endswith('/stable/historical-price-eod/dividend-adjusted'), url)
        self.assertEqual(
            params,
            {'symbol': 'AAPL', 'from': '2025-01-02', 'to': '2025-01-10', 'apikey': SECRET},
        )
        self.assertEqual(timeout, 30.0)
        self.assertEqual(len(bars), 2)

    def test_dates_are_optional(self):
        fmp, session, _ = provider([FakeResponse(200, [])])
        fmp.daily_bars('AAPL')
        self.assertEqual(session.calls[0][1], {'symbol': 'AAPL', 'apikey': SECRET})

    def test_retries_with_exponential_backoff_on_429(self):
        fmp, session, sleeps = provider([FakeResponse(429), FakeResponse(500), FakeResponse(200, ADJ_RECORDS)])
        self.assertEqual(len(fmp.daily_bars('AAPL')), 2)
        self.assertEqual(len(session.calls), 3)
        self.assertEqual(sleeps, [1.0, 2.0])

    def test_gives_up_after_max_retries(self):
        fmp, session, sleeps = provider([FakeResponse(503)] * 3, max_retries=2)
        with self.assertRaises(ProviderError) as ctx:
            fmp.daily_bars('AAPL')
        self.assertIn('3 intentos', str(ctx.exception))
        self.assertEqual(len(session.calls), 3)
        self.assertEqual(sleeps, [1.0, 2.0])

    def test_client_errors_are_not_retried_and_never_leak_the_key(self):
        body = {'Error Message': f'Invalid API KEY {SECRET}. Please retry.'}
        fmp, session, sleeps = provider([FakeResponse(401, body)])
        with self.assertRaises(ProviderError) as ctx:
            fmp.daily_bars('AAPL')
        self.assertIn('HTTP 401', str(ctx.exception))
        self.assertNotIn(SECRET, str(ctx.exception))
        self.assertEqual(len(session.calls), 1)
        self.assertEqual(sleeps, [])

    def test_network_errors_are_retried_and_do_not_leak_url_or_key(self):
        boom = requests.ConnectionError(f'Max retries exceeded with url: /stable/x?apikey={SECRET}')
        fmp, session, _ = provider([boom] * 4)
        with self.assertRaises(ProviderError) as ctx:
            fmp.daily_bars('AAPL')
        message = str(ctx.exception)
        self.assertNotIn(SECRET, message)
        self.assertNotIn('apikey', message)
        self.assertIn('ConnectionError', message)
        self.assertEqual(len(session.calls), 4)

    def test_error_message_inside_a_200_body_raises(self):
        fmp, _, _ = provider([FakeResponse(200, {'Error Message': f'Limit Reach {SECRET}'})])
        with self.assertRaises(ProviderError) as ctx:
            fmp.daily_bars('AAPL')
        self.assertIn('Limit Reach', str(ctx.exception))
        self.assertNotIn(SECRET, str(ctx.exception))

    def test_non_json_answer_raises(self):
        fmp, _, _ = provider([FakeResponse(200, None)])
        with self.assertRaises(ProviderError):
            fmp.daily_bars('AAPL')

    def test_missing_api_key_is_rejected(self):
        for key in ('', None):
            with self.subTest(key=key), self.assertRaises(ProviderError):
                FMPProvider(key)

    def test_profile_request(self):
        fmp, session, _ = provider([FakeResponse(200, [{'companyName': 'Apple Inc.', 'sector': 'Technology'}])])
        profile = fmp.profile('AAPL')
        self.assertTrue(session.calls[0][0].endswith('/stable/profile'))
        self.assertEqual(session.calls[0][1], {'symbol': 'AAPL', 'apikey': SECRET})
        self.assertEqual((profile.name, profile.sector), ('Apple Inc.', 'Technology'))


class ReadCsvTests(unittest.TestCase):
    def write(self, name, text):
        path = Path(self.tmp.name) / name
        path.write_text(text, encoding='utf-8')
        return path

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)

    def test_yfinance_style_headers_rescale_ohlc_with_the_adjustment_factor(self):
        path = self.write('AAPL.csv', (
            'Date,Open,High,Low,Close,Adj Close,Volume\n'
            '2025-01-03,102,104,100,103,51.5,1000\n'
            '2025-01-02,100,102,99,101,50.5,2000\n'
        ))
        bars = read_price_csv(path)
        self.assertEqual([b.date for b in bars], [dt.date(2025, 1, 2), dt.date(2025, 1, 3)])
        first = bars[0]  # factor 50.5 / 101 = 0.5
        self.assertEqual((first.adj_open, first.adj_high, first.adj_low, first.adj_close, first.volume),
                         (50.0, 51.0, 49.5, 50.5, 2000))

    def test_close_only_is_taken_as_already_adjusted(self):
        path = self.write('X.csv', 'date,open,close\n2025-01-02,9,10\n')
        bar = read_price_csv(path)[0]
        self.assertEqual((bar.adj_open, bar.adj_close, bar.adj_high, bar.volume), (9.0, 10.0, None, None))

    def test_missing_columns_raise(self):
        with self.assertRaises(ProviderError):
            read_price_csv(self.write('a.csv', 'fecha,close\n2025-01-02,10\n'))
        with self.assertRaises(ProviderError):
            read_price_csv(self.write('b.csv', 'date,volume\n2025-01-02,10\n'))

    def test_unparseable_dates_raise_instead_of_returning_nothing(self):
        path = self.write('c.csv', 'date,close\n02/01/2025,10\n')
        with self.assertLogs('engine.data.providers', level='WARNING'), self.assertRaises(ProviderError):
            read_price_csv(path)

    def test_missing_file_raises_provider_error(self):
        with self.assertRaises(ProviderError):
            read_price_csv(Path(self.tmp.name) / 'no-existe.csv')


if __name__ == '__main__':
    unittest.main()
