import json
from io import StringIO

from django.core.management import call_command
from django.test import TestCase, override_settings
from django.urls import reverse

from backtests import services
from backtests.models import BacktestRun, SelectionCache
from engine.backtest.config import BacktestConfig, PairsRules
from market.importers import load_demo

PAIRS = BacktestConfig(strategy='pairs', rules=PairsRules(formation=252, trading=63))


@override_settings(JOBS_MODE='sync')
class BacktestFlowTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.universe = load_demo(n=800)

    def test_reversal_run_stores_results(self):
        run = services.create_run(BacktestConfig.default('reversal'), self.universe)
        run.refresh_from_db()
        self.assertTrue(run.is_done, run.job.error)
        self.assertIn('sharpe', run.metrics)
        self.assertEqual(len(run.daily['dates']), len(run.daily['equity']))
        self.assertTrue(run.attribution['by_group'])
        self.assertTrue(run.fingerprint)
        json.dumps(run.metrics, allow_nan=False)  # JSON estricto, sin NaN

    def test_pairs_run_stores_trades_and_reuses_the_selection(self):
        first = services.create_run(PAIRS, self.universe)
        first.refresh_from_db()
        self.assertTrue(first.is_done, first.job.error)
        self.assertGreater(first.trades.count(), 0)
        self.assertEqual(SelectionCache.objects.count(), 1)
        second = services.create_run(PAIRS.with_rules(entry_z=2.5), self.universe)
        second.refresh_from_db()
        self.assertEqual(SelectionCache.objects.count(), 1)
        self.assertEqual(first.selection_key, second.selection_key)

    def test_robustness_variants_and_gate(self):
        run = services.create_run(BacktestConfig.default('reversal'), self.universe)
        self.assertEqual(services.launch_robustness(run), 6)
        self.assertEqual(services.launch_robustness(run), 0)
        summary = services.robustness_summary(run)
        self.assertTrue(summary['done'])
        self.assertIn('passed', summary['gate'])
        self.assertEqual(len(summary['rows']), 7)

    def test_a_failing_backtest_is_reported(self):
        config = BacktestConfig(strategy='pairs', rules=PairsRules(formation=1000))  # más que las 800 sesiones
        with self.assertLogs('core.jobs', 'ERROR'):
            run = services.create_run(config, self.universe)
        run.refresh_from_db()
        self.assertEqual(run.status, 'failed')
        self.assertIn('Datos insuficientes', run.job.error)


@override_settings(JOBS_MODE='sync')
class ViewTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.universe = load_demo(n=800)
        cls.reversal = services.create_run(BacktestConfig.default('reversal'), cls.universe)
        cls.pairs = services.create_run(PAIRS, cls.universe)

    def form_data(self, **changes):
        data = {
            'strategy': 'reversal', 'name': '', 'universe': self.universe.pk, 'capital': 100000, 'execution': 'next_close',
            'commission_bps': 1, 'slippage_bps': 5, 'borrow_bps_annual': 50, 'lookback': 5, 'rebalance': 5,
            'vol_window': 60, 'gross': 1, 'max_weight': 0.05, 'min_gap': 0, 'min_price': 5, 'min_dollar_volume_m': 0,
            'formation': 252, 'trading': 63, 'max_pairs': 10, 'gross_per_pair': 0.2, 'entry_z': 2, 'exit_z': 0.5,
            'stop_z': 4, 'max_hold_hl': 3, 'min_corr': 0.7, 'max_p_adj': 0.05, 'max_half_life': 30, 'require_cross': 'on',
        }
        data.update(changes)
        return data

    def test_pages_render(self):
        urls = [
            reverse('backtests:list'), reverse('backtests:new'), reverse('backtests:new') + '?strategy=pairs',
            reverse('backtests:new') + f'?from={self.pairs.pk}', reverse('backtests:detail', args=[self.reversal.pk]),
            reverse('backtests:detail', args=[self.pairs.pk]), reverse('backtests:compare'),
            reverse('backtests:compare') + f'?ids={self.reversal.pk}&ids={self.pairs.pk}',
            reverse('backtests:scanner'), reverse('backtests:scanner') + f'?universe={self.universe.pk}&lookback=252&min_corr=0.7&max_p=0.05&entry_z=2',
            reverse('backtests:pair') + f'?universe={self.universe.pk}&y=DEMO-P1Y&x=DEMO-P1X&lookback=252',
            reverse('core:dashboard'),
        ]
        for url in urls:
            with self.subTest(url=url):
                self.assertEqual(self.client.get(url).status_code, 200)

    def test_detail_contains_chart_data_and_metrics(self):
        response = self.client.get(reverse('backtests:detail', args=[self.pairs.pk]))
        self.assertContains(response, 'id="equity-chart"')
        self.assertContains(response, 'Coste de break-even')
        self.assertContains(response, 'Motivo de salida')

    def test_create_from_the_form(self):
        response = self.client.post(reverse('backtests:new'), self.form_data(name='Mi prueba'))
        run = BacktestRun.objects.get(name='Mi prueba')
        self.assertRedirects(response, reverse('backtests:detail', args=[run.pk]))
        self.assertTrue(run.is_done)
        self.assertEqual(run.config['rules']['lookback'], 5)

    def test_invalid_rules_show_an_error(self):
        response = self.client.post(reverse('backtests:new'), self.form_data(strategy='pairs', exit_z=3))
        self.assertContains(response, 'exit_z')
        self.assertFalse(BacktestRun.objects.filter(config__rules__exit_z=3).exists())

    def test_robustness_csv_and_delete(self):
        self.client.post(reverse('backtests:robustness', args=[self.pairs.pk]))
        self.assertEqual(self.pairs.variants.count(), 6)
        response = self.client.get(reverse('backtests:detail', args=[self.pairs.pk]))
        self.assertContains(response, 'Puerta de decisión')
        trades = self.client.get(reverse('backtests:trades_csv', args=[self.pairs.pk]))
        self.assertEqual(trades.content.decode().splitlines()[0].split(',')[0], 'trade_id')
        equity = self.client.get(reverse('backtests:equity_csv', args=[self.pairs.pk]))
        self.assertTrue(equity.content.decode().startswith('date,equity'))
        self.client.post(reverse('backtests:delete', args=[self.pairs.pk]))
        self.assertFalse(BacktestRun.objects.filter(pk=self.pairs.pk).exists())

    def test_pair_page_with_unknown_tickers_shows_an_error(self):
        response = self.client.get(reverse('backtests:pair') + f'?universe={self.universe.pk}&y=XXX&x=DEMO-P1X')
        self.assertContains(response, 'XXX no está en este universo')


@override_settings(JOBS_MODE='sync')
class CommandTests(TestCase):
    def test_run_backtest(self):
        load_demo(n=600)
        out = StringIO()
        call_command('run_backtest', 'Demo (sintético)', stdout=out)
        self.assertIn('Sharpe', out.getvalue())
