from core.jobs import register

from .services import execute


@register('backtest')
def backtest_job(job, reporter):
    run = execute(job.payload['run_id'], reporter.progress)
    metrics = run.metrics
    sharpe = metrics.get('sharpe')
    cagr = metrics.get('cagr')
    parts = []
    if sharpe is not None:
        parts.append(f'Sharpe {sharpe:.2f}'.replace('.', ','))
    if cagr is not None:
        parts.append(f'CAGR {100 * cagr:.1f} %'.replace('.', ','))
    return {'message': ' · '.join(parts) or 'Completado', 'run_id': run.pk}
