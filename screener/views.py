from django.shortcuts import render, redirect
from django.views.decorators.http import require_POST
#from .models import Stock
from .forms import StockFilterForm
from django.shortcuts import get_object_or_404
import plotly.graph_objs as go
from plotly.offline import plot
from datetime import datetime
from django.utils.timezone import now

def screener_view(request):
    form = StockFilterForm(request.GET or None)
    stocks = Stock.objects.all()

    if form.is_valid():
        sector = form.cleaned_data.get('sector')
        min_cap = form.cleaned_data.get('min_market_cap')
        max_cap = form.cleaned_data.get('max_market_cap')

        if sector:
            stocks = stocks.filter(sector__icontains=sector)
        if min_cap is not None:
            stocks = stocks.filter(market_cap__gte=min_cap)
        if max_cap is not None:
            stocks = stocks.filter(market_cap__lte=max_cap)

    return render(request, 'screener/screener.html', {
        'form': form,
        'stocks': stocks,
    })

@require_POST
def importar_view(request):
    # Obtenir tickers des del formulari (per exemple textarea)
    tickers_text = request.POST.get('tickers', '')
    tickers = [t.strip().upper() for t in tickers_text.split(',') if t.strip()]

    if tickers:
        #importar_dades_yfinance(tickers)
        importar_dades_alphavantage(tickers)

    return redirect('screener')


def generate_chart(fundamentals, ticker, field, title, color):
    # Filtrar dades vàlides abans de l’any actual
    data = [
        (f.date.year, getattr(f, field))
        for f in fundamentals
        if getattr(f, field) is not None and f.date.year < now().year
    ]

    # Sumar per any
    data_dict = {}
    for year, val in data:
        data_dict[year] = data_dict.get(year, 0) + val
    sorted_data = sorted(data_dict.items())

    # Detectar grans buits (només per dividends)
    start_index = 0
    if field == 'dividend':
        for i in range(1, len(sorted_data)):
            if sorted_data[i][0] - sorted_data[i - 1][0] > 5:
                start_index = i
    filtered_data = sorted_data[start_index:]

    years = [y for y, v in filtered_data]
    values = [v for y, v in filtered_data]

    bar = go.Bar(x=years, y=values, marker_color=color)

    small_layout = go.Layout(
        autosize=True,
        margin=dict(l=30, r=30, t=30, b=30),
        template='plotly_white',
        title='',
        xaxis_title='Any',
        yaxis_title=title,
        showlegend=False,
        xaxis=dict(tickangle=-45),
        height=280
    )
    fig_small = go.Figure(data=[bar], layout=small_layout)
    plot_div_small = plot(fig_small, output_type='div', include_plotlyjs=False,
                          config={'displayModeBar': False, 'responsive': True})


    
    large_layout = go.Layout(
        height=500, width=900, margin=dict(l=50, r=50, t=50, b=50),
        template='plotly_white', title=f'{title} de {ticker}',
        xaxis_title='Any', yaxis_title=title
    )
    fig_large = go.Figure(data=[bar], layout=large_layout)
    plot_div_large = plot(fig_large, output_type='div', include_plotlyjs=False,
                            config={'displayModeBar': False})
        

    result = {'title': title, 'plot_div_small': plot_div_small, 'plot_div_large': plot_div_large}

    return result

def stock_detail_view(request, ticker):
    stock = get_object_or_404(Stock, ticker=ticker.upper())
    fundamentals = stock.fundamental_set.order_by('-date')

    charts = [
        generate_chart(fundamentals, stock.ticker, 'dividend', 'Dividend', 'green'),
        generate_chart(fundamentals, stock.ticker, 'price', 'Precio', 'blue'),
        generate_chart(fundamentals, stock.ticker, 'pe_ratio', 'PER', 'orange'),
    ]

    context = {
        'stock': stock,
        'fundamentals': fundamentals,
        'charts': charts,
    }
    return render(request, 'screener/stock_detail.html', context)