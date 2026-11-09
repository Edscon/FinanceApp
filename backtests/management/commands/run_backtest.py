"""Ejecuta un backtest desde la línea de comandos (sin servidor) y muestra el resumen."""
import datetime as dt

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError

from backtests import services
from engine.backtest.config import BacktestConfig
from engine.backtest.robustness import gate, variants
from market.models import Universe


class Command(BaseCommand):
    help = 'Backtest con los parámetros por defecto (o los indicados) sobre un universo con datos.'

    def add_arguments(self, parser):
        parser.add_argument('universe', help='Nombre del universo, p. ej. "US grandes valores"')
        parser.add_argument('--strategy', choices=['reversal', 'pairs'], default='reversal')
        parser.add_argument('--from', dest='start', help='YYYY-MM-DD')
        parser.add_argument('--to', dest='end', help='YYYY-MM-DD')
        parser.add_argument('--execution', choices=['next_close', 'next_open'], default='next_close')
        parser.add_argument('--robustness', action='store_true', help='Ejecuta también las variantes de robustez')

    def handle(self, *args, **options):
        settings.JOBS_MODE = 'sync'  # en la línea de comandos, el backtest se ejecuta aquí mismo
        try:
            universe = Universe.objects.get(name=options['universe'])
        except Universe.DoesNotExist:
            raise CommandError(f'No existe el universo «{options["universe"]}».') from None
        config = BacktestConfig.default(
            options['strategy'], execution=options['execution'],
            start=dt.date.fromisoformat(options['start']) if options['start'] else None,
            end=dt.date.fromisoformat(options['end']) if options['end'] else None,
        )
        run = self._run(config, universe)
        if options['robustness']:
            variant_metrics = {}
            for key, label, variant in variants(config):
                child = services.create_run(variant, universe, name=run.name, parent=run, variant_key=key, variant_label=label)
                child.refresh_from_db()
                self._print(label, child)
                variant_metrics[key] = child.metrics
            verdict = gate(variant_metrics)
            self.stdout.write(f"\nPuerta (Sharpe > {verdict['threshold']} con costes x1,5): "
                              f"{'SUPERADA' if verdict['passed'] else 'NO superada'}")

    def _run(self, config, universe):
        run = services.create_run(config, universe)
        run.refresh_from_db()
        self._print('Base', run)
        return run

    def _print(self, label, run):
        if not run.is_done:
            raise CommandError(f'El backtest #{run.pk} no terminó ({run.status}).\n' + (run.job.error if run.job else ''))
        m = run.metrics
        fmt = lambda v, pct=False: '—' if v is None else (f'{100 * v:.2f} %' if pct else f'{v:.2f}')  # noqa: E731
        self.stdout.write(
            f"[{label}] #{run.pk} {m['start']}→{m['end']} · CAGR {fmt(m.get('cagr'), True)} · "
            f"Sharpe {fmt(m.get('sharpe'))} ± {fmt(m.get('sharpe_se'))} · maxDD {fmt(m.get('max_drawdown'), True)} · "
            f"rotación {fmt(m.get('turnover'))}x · break-even {fmt(m.get('break_even_bps'))} pb"
        )
