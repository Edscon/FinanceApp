"""Datos del panel principal."""
from .models import Job


def dashboard_context() -> dict:
    context = {'jobs': Job.objects.filter(status__in=Job.ACTIVE)[:5]}
    try:
        from market.services import coverage_summary

        context['coverage'] = coverage_summary()
    except ImportError:  # pragma: no cover
        context['coverage'] = None
    try:
        from backtests.models import BacktestRun

        context['runs'] = BacktestRun.objects.filter(parent__isnull=True).select_related('job').defer(
            'daily', 'windows', 'attribution'
        )[:6]
    except ImportError:  # pragma: no cover
        context['runs'] = []
    return context
