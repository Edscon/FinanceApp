import datetime as dt

from django.contrib import messages
from django.http import HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_POST

from core import charts
from engine.backtest.config import BacktestConfig
from engine.research.pairs import trade_legs
from market.models import Universe

from . import services
from .forms import BacktestForm, ScannerForm
from .models import BacktestRun


def runs_list(request):
    runs = BacktestRun.objects.filter(parent__isnull=True).select_related('job').defer('daily', 'windows', 'attribution')
    return render(request, 'backtests/list.html', {'runs': runs})


def new(request):
    if request.method == 'POST':
        form = BacktestForm(request.POST)
        if form.is_valid():
            run = services.create_run(form.cleaned_data['config'], form.cleaned_data['universe'], name=form.cleaned_data['name'])
            return redirect('backtests:detail', pk=run.pk)
    else:
        initial = BacktestForm.initial_from(BacktestConfig.default(request.GET.get('strategy', 'reversal')
                                                                   if request.GET.get('strategy') in ('reversal', 'pairs') else 'reversal'))
        source = request.GET.get('from')
        if source:
            original = get_object_or_404(BacktestRun, pk=source)
            initial = BacktestForm.initial_from(original.config_obj(), original.universe, f'{original.name} (copia)')
        if request.GET.get('universe'):
            initial['universe'] = request.GET['universe']
        form = BacktestForm(initial=initial)
    has_data = Universe.objects.filter(members__instrument__n_bars__gt=0).exists()
    return render(request, 'backtests/new.html', {'form': form, 'has_data': has_data})


def detail(request, pk):
    run = get_object_or_404(BacktestRun.objects.select_related('job', 'universe', 'parent'), pk=pk)
    context = {'run': run, 'job': run.job, 'trials': services.trials_count(run)}
    if run.is_done:
        best, worst = services.ranked_tickers(run)
        sharpe, se = run.metrics.get('sharpe'), run.metrics.get('sharpe_se')
        context.update({
            'm': run.metrics,
            'sharpe_low': sharpe - 1.96 * se if sharpe is not None and se is not None else None,
            'sharpe_high': sharpe + 1.96 * se if sharpe is not None and se is not None else None,
            'equity_chart': services.equity_chart(run),
            'exposure_chart': services.exposure_chart(run),
            'monthly': services.monthly_rows(run.metrics),
            'robustness': services.robustness_summary(run) if run.parent_id is None else None,
            'groups': services.groups_table(run),
            'best': best,
            'worst': worst,
            'trades': run.trades.all()[:300],
            'n_trades': run.trades.count(),
            'exit_reasons': sorted((run.attribution.get('by_exit_reason') or {}).items(), key=lambda kv: -kv[1]['n']),
            'reason_labels': services.EXIT_REASONS,
            'config_rows': services.config_rows(run.config),
        })
    return render(request, 'backtests/detail.html', context)


@require_POST
def robustness(request, pk):
    run = get_object_or_404(BacktestRun, pk=pk, parent__isnull=True)
    if not run.is_done:
        messages.error(request, 'Espera a que termine el backtest.')
    elif run.universe is None:
        messages.error(request, 'El universo de este backtest ya no existe.')
    else:
        launched = services.launch_robustness(run)
        messages.success(request, f'Lanzadas {launched} variantes de robustez.' if launched else 'Las variantes ya existían.')
    return redirect('backtests:detail', pk=run.pk)


@require_POST
def delete(request, pk):
    run = get_object_or_404(BacktestRun, pk=pk)
    parent = run.parent_id
    run.delete()
    messages.success(request, 'Backtest eliminado.')
    return redirect('backtests:detail', pk=parent) if parent else redirect('backtests:list')


def trades_csv(request, pk):
    run = get_object_or_404(BacktestRun, pk=pk)
    response = HttpResponse(services.trades_csv(run), content_type='text/csv; charset=utf-8')
    response['Content-Disposition'] = f'attachment; filename="backtest-{run.pk}-operaciones.csv"'
    return response


def equity_csv(request, pk):
    run = get_object_or_404(BacktestRun, pk=pk)
    response = HttpResponse(services.equity_csv(run), content_type='text/csv; charset=utf-8')
    response['Content-Disposition'] = f'attachment; filename="backtest-{run.pk}-equity.csv"'
    return response


def compare(request):
    ids = [int(i) for i in request.GET.getlist('ids') if i.isdigit()]
    runs = list(BacktestRun.objects.filter(pk__in=ids).select_related('job'))
    runs = [r for r in runs if r.is_done]
    payload = None
    if runs:
        series = []
        for k, run in enumerate(runs):
            equity = run.daily['equity']
            base = equity[0] or 1
            series.append(charts.series(run.daily['dates'], [100 * v / base for v in equity], f'#{run.pk}',
                                        charts.PALETTE[k % len(charts.PALETTE)]))
        payload = charts.chart(*series)
    choices = BacktestRun.objects.filter(parent__isnull=True).select_related('job').defer('daily', 'windows', 'attribution')[:50]
    return render(request, 'backtests/compare.html', {
        'runs': runs, 'chart': payload, 'choices': [c for c in choices if c.is_done], 'selected': ids,
        'colors': charts.PALETTE,
    })


def scanner(request):
    form = ScannerForm(request.GET or None)
    report, error, rows = None, None, []
    if request.GET and form.is_valid():
        c = form.cleaned_data
        try:
            report = services.scan(c['universe'], c['as_of'], c['lookback'], c['min_corr'], c['max_p'])
        except ValueError as exc:
            error = str(exc)
        else:
            rows = sorted(report.pairs, key=lambda p: -abs(p.z))
            rows = [(p, trade_legs(p, c['entry_z'])) for p in rows]
    return render(request, 'backtests/scanner.html', {'form': form, 'report': report, 'rows': rows, 'error': error})


def pair(request):
    universe = get_object_or_404(Universe, pk=request.GET.get('universe'))
    y, x = request.GET.get('y', ''), request.GET.get('x', '')
    as_of = request.GET.get('as_of') or None
    lookback = int(request.GET.get('lookback') or 504)
    try:
        analysis = services.pair_analysis(universe, y, x, dt.date.fromisoformat(as_of) if as_of else None, lookback)
        error = None
    except ValueError as exc:
        analysis, error = None, str(exc)
    return render(request, 'backtests/pair.html', {
        'universe': universe, 'y': y, 'x': x, 'analysis': analysis, 'error': error, 'lookback': lookback,
    })
