import datetime as dt

from core.jobs import register

from .importers import import_prices, load_demo


def _date(value):
    return dt.date.fromisoformat(value) if value else None


@register('import_prices')
def import_prices_job(job, reporter):
    payload = job.payload
    reporter.progress(0.01, 'Conectando con el proveedor')
    return import_prices(
        payload['tickers'],
        payload.get('provider', 'yahoo'),
        _date(payload.get('start')),
        _date(payload.get('end')),
        progress=reporter.progress,
    )


@register('load_demo')
def load_demo_job(job, reporter):
    reporter.progress(0.1, 'Generando el mercado sintético')
    universe = load_demo()
    return {'message': f'Demo cargada: {universe.members.count()} valores sintéticos', 'universe_id': universe.pk}
