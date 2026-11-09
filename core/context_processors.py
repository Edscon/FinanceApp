from .models import Job

SECTIONS = {
    'core:dashboard': 'panel',
    'core:jobs': 'trabajos',
    'market': 'datos',
    'market:universes': 'universos',
    'market:universe': 'universos',
    'backtests:scanner': 'escaner',
    'backtests:pair': 'escaner',
    'backtests:compare': 'comparar',
    'backtests': 'backtests',
}


def app_context(request):
    match = getattr(request, 'resolver_match', None)
    section = ''
    if match:
        full = f'{match.namespace}:{match.url_name}' if match.namespace else match.url_name
        section = SECTIONS.get(full) or SECTIONS.get(match.namespace or '', '')
    return {
        'section': section,
        'active_jobs': Job.objects.filter(status__in=Job.ACTIVE).count(),
    }
