from django.shortcuts import render, redirect
from django.views.decorators.http import require_POST
from .models import Company
from .forms import StockFilterForm
from django.shortcuts import get_object_or_404
import plotly.graph_objs as go
from plotly.offline import plot
from datetime import datetime
from django.utils.timezone import now

def screener_view(request):
    form = StockFilterForm(request.GET or None)
    stocks = Company.objects.all()

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

# Stock View
def generate_chart(fundamentals, ticker, field, title, color, frequency='yearly'):
    current_year = now().year
    current_quarter = (now().month - 1) // 3 + 1

    quarter_map = {'Q1': '01', 'Q2': '02', 'Q3': '03', 'Q4': '04'}
    quarter_label = {'Q1': 'Q1', 'Q2': 'Q2', 'Q3': 'Q3', 'Q4': 'Q4'}

    data = []

    for f in fundamentals:
        value = getattr(f, field)
        if value is None:
            continue

        if frequency == 'yearly':
            if f.fiscal_year < current_year:
                data.append((f.fiscal_year, value))

        elif frequency == 'quarterly':
            year = f.fiscal_year
            period = f.period
            if period not in quarter_map:
                continue

            year_period_int = int(f"{year}{quarter_map[period]}")
            max_year_period = int(f"{current_year}{current_quarter:02d}")

            if year_period_int < max_year_period:
                key = f"{year}-Q{quarter_map[period]}"  # ex: 2024-Q1
                data.append((key, value))

    if frequency == 'yearly':
        # Sumar per any
        data_dict = {}
        for year, val in data:
            data_dict[year] = data_dict.get(year, 0) + val
        sorted_data = sorted(data_dict.items())

    elif frequency == 'quarterly':
        def quarter_key(qstr):
            year, q = qstr.split('-Q')
            return int(year) * 10 + int(q)

        sorted_data = sorted(data, key=lambda x: quarter_key(x[0]))

    # Detectar grans buits només per dividends
    start_index = 0
    if field == 'adj_dividend':
        if frequency == 'yearly':
            for i in range(1, len(sorted_data)):
                if int(sorted_data[i][0]) - int(sorted_data[i - 1][0]) > 5:
                    start_index = i

        elif frequency == 'quarterly':
            def next_expected(period_str):
                year, q = period_str.split('-Q')
                year, quarter = int(year), int(q)
                if quarter == 4:
                    return f"{year + 1}-Q1"
                else:
                    return f"{year}-Q{quarter + 1}"

            for i in range(1, len(sorted_data)):
                expected = next_expected(sorted_data[i - 1][0])
                if sorted_data[i][0] != expected:
                    start_index = i

    filtered_data = sorted_data[start_index:]

    x_labels = [x for x, _ in filtered_data]
    y_values = [y for _, y in filtered_data]

    bar = go.Bar(x=x_labels, y=y_values, marker_color=color)

    # Small chart
    small_layout = go.Layout(
        autosize=True,
        margin=dict(l=30, r=30, t=30, b=30),
        template='plotly_white',
        title='',
        xaxis_title='Període',
        yaxis_title=title,
        showlegend=False,
        xaxis=dict(tickangle=-45),
        height=280
    )
    fig_small = go.Figure(data=[bar], layout=small_layout)
    plot_div_small = plot(fig_small, output_type='div', include_plotlyjs=False,
                          config={'displayModeBar': False, 'responsive': True})

    # Large chart
    large_layout = go.Layout(
        height=500, width=900, margin=dict(l=50, r=50, t=50, b=50),
        template='plotly_white',
        title=f'{title} de {ticker}',
        xaxis_title='Període',
        yaxis_title=title
    )
    fig_large = go.Figure(data=[bar], layout=large_layout)
    plot_div_large = plot(fig_large, output_type='div', include_plotlyjs=False,
                          config={'displayModeBar': False})

    return {
        'title': title,
        'plot_div_small': plot_div_small,
        'plot_div_large': plot_div_large
    }

def stock_detail_view(request, ticker):
    stock = get_object_or_404(Company, ticker=ticker.upper())
    fundamentals = stock.quarters.order_by('-period_end')  # relació nova

    charts = [
        generate_chart(fundamentals, stock.ticker, 'adj_dividend', 'Dividend', 'green'),
        generate_chart(fundamentals, stock.ticker, 'eps', 'EPS', 'blue'),
        generate_chart(fundamentals, stock.ticker, 'revenue', 'Ingresos', 'orange'),
    ]

    context = {
        'stock': stock,
        'fundamentals': fundamentals,
        'charts': charts,
    }
    return render(request, 'screener/stock_detail.html', context)