from django.contrib import messages
from django.db.models import Count, Q
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_POST

from core import charts
from core.jobs import enqueue
from core.models import Job

from .forms import ImportForm, UniverseForm
from .importers import PROVIDERS
from .models import Instrument, Universe
from .services import coverage_summary, load_presets, set_members

IMPORT_KINDS = ['import_prices', 'load_demo']


def data(request):
    form = ImportForm(request.POST or None)
    if request.method == 'POST' and form.is_valid():
        tickers = form.cleaned_data['ticker_list']
        provider = form.cleaned_data['provider']
        enqueue(
            'import_prices',
            {'tickers': tickers, 'provider': provider, 'start': form.cleaned_data['start'], 'end': form.cleaned_data['end']},
            label=f'Importar {len(tickers)} valores desde {PROVIDERS[provider]}',
        )
        messages.success(request, f'Importación de {len(tickers)} valores en marcha. Puedes seguir usando la app.')
        return redirect('market:data')
    instruments = Instrument.objects.filter(n_bars__gt=0).annotate(n_universes=Count('memberships'))
    return render(request, 'market/data.html', {
        'form': form,
        'instruments': instruments,
        'without_data': Instrument.objects.filter(n_bars=0).count(),
        'coverage': coverage_summary(),
        'jobs': Job.objects.filter(kind__in=IMPORT_KINDS)[:4],
        'has_presets': Universe.objects.filter(is_preset=True).exclude(name__startswith='Demo').exists(),
    })


@require_POST
def demo(request):
    enqueue('load_demo', label='Cargar datos de demostración (sintéticos)')
    messages.success(request, 'Generando los datos de demostración: tardará unos segundos.')
    return redirect('market:data')


@require_POST
def presets(request):
    universes = load_presets()
    messages.success(request, f'Universos predefinidos listos: {", ".join(u.name for u in universes)}. '
                              'Ahora importa sus precios.')
    return redirect('market:universes')


def instrument(request, ticker):
    obj = get_object_or_404(Instrument, ticker=ticker)
    rows = list(obj.bars.order_by('date').values_list('date', 'adj_close', 'volume'))
    dates = [r[0] for r in rows]
    payload = charts.chart(
        charts.series(dates, [r[1] for r in rows], 'Cierre ajustado', charts.GREEN),
        charts.series(dates, [r[2] for r in rows], 'Volumen', charts.SLATE, pane=1, kind='histogram', fmt='volume'),
        stretch=[3, 1],
    )
    return render(request, 'market/instrument.html', {
        'instrument': obj, 'chart': payload, 'memberships': obj.memberships.select_related('universe'),
    })


def universes(request):
    items = Universe.objects.annotate(
        n_members=Count('members'), n_with_data=Count('members', filter=Q(members__instrument__n_bars__gt=0))
    )
    return render(request, 'market/universes.html', {'universes': items})


def universe_detail(request, pk):
    universe = get_object_or_404(Universe, pk=pk)
    initial = {'members': '\n'.join(f'{m.instrument.ticker}, {m.group}' for m in universe.members.select_related('instrument'))}
    form = UniverseForm(request.POST or None, instance=universe, initial=initial)
    if request.method == 'POST' and form.is_valid():
        form.save()
        set_members(universe, form.cleaned_data['members'])
        messages.success(request, 'Universo guardado.')
        return redirect('market:universe', pk=universe.pk)
    members = universe.members.select_related('instrument').order_by('group', 'instrument__ticker')
    return render(request, 'market/universe_detail.html', {
        'universe': universe, 'members': members, 'form': form,
        'missing': [m.instrument.ticker for m in members if not m.instrument.n_bars],
        'is_demo': universe.name.startswith('Demo'),
    })


def universe_new(request):
    form = UniverseForm(request.POST or None)
    if request.method == 'POST' and form.is_valid():
        universe = form.save()
        set_members(universe, form.cleaned_data['members'])
        messages.success(request, 'Universo creado. Importa sus precios para poder usarlo.')
        return redirect('market:universe', pk=universe.pk)
    return render(request, 'market/universe_form.html', {'form': form})


@require_POST
def universe_delete(request, pk):
    universe = get_object_or_404(Universe, pk=pk)
    universe.delete()
    messages.success(request, f'Universo «{universe.name}» eliminado (los precios se conservan).')
    return redirect('market:universes')


@require_POST
def universe_import(request, pk):
    universe = get_object_or_404(Universe, pk=pk)
    tickers = universe.tickers() + ([universe.benchmark] if universe.benchmark else [])
    enqueue('import_prices', {'tickers': tickers, 'provider': 'yahoo', 'start': '2010-01-01', 'end': None},
            label=f'Importar «{universe.name}» desde Yahoo Finance')
    messages.success(request, f'Descargando {len(tickers)} valores desde Yahoo Finance (2010 en adelante).')
    return redirect('market:data')
