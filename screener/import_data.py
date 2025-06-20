import yfinance as yf
from datetime import datetime
from .models import Stock, Fundamental
import pandas as pd
import requests

def importar_dades_yfinance(tickers):
    for ticker in tickers:
        try:
            print(f"Processant {ticker}...")
            ticker_obj = yf.Ticker(ticker)
            stock_info = ticker_obj.info

            stock, _ = Stock.objects.get_or_create(
                ticker=ticker,
                defaults={
                    'name': stock_info.get('shortName', ticker),
                    'sector': stock_info.get('sector', 'Desconegut'),
                }
            )


            financials = ticker_obj.quarterly_financials
            print(financials)
            hist = ticker_obj.history(period="max", interval="3mo")

            for date, row in hist.iterrows():
                date_obj = date.date()

                revenue = financials.loc['Total Revenue', date_obj] if 'Total Revenue' in financials.index else None
                net_income = financials.loc['Net Income', date_obj] if 'Net Income' in financials.index else None


                Fundamental.objects.update_or_create(
                    stock=stock,
                    date=date_obj,
                    defaults={
                        'price': row['Close'],
                        'dividend': row.get('Dividends', 0),  # pot no existir, poso 0 per defecte
                        'revenue': revenue,
                        'net_income': net_income,
                    }
                )

            print(f"{ticker} processat correctament.")
        except Exception as e:
            print(f"Error amb {ticker}: {e}")



def importar_dades_alphavantage(tickers):
    for ticker in tickers:
        try:
            print(f"Processant {ticker}...")

            apikey = '7MS7OVS1Y09OJLYK'
            apikey = 'demo'

            url = f'https://www.alphavantage.co/query?function=OVERVIEW&symbol={ticker}&apikey={apikey}'
            r = requests.get(url)
            data_OVERVIEW = r.json()

            stock, _ = Stock.objects.get_or_create(
                ticker=data_OVERVIEW['Symbol'],
                defaults={
                    'name': data_OVERVIEW['Name'],  # Nom per defecte, pots canviar-ho si tens més informació
                    'sector': data_OVERVIEW['Sector'],  # Sector per defecte, pots canviar-ho si tens més informació
                }
            )

            # replace the "demo" apikey below with your own key from https://www.alphavantage.co/support/#api-key
            url = f'https://www.alphavantage.co/query?function=INCOME_STATEMENT&symbol={ticker}&apikey={apikey}'
            r = requests.get(url)
            data_INCOME_STATEMENT = r.json()

            url = f'https://www.alphavantage.co/query?function=DIVIDENDS&symbol={ticker}&apikey={apikey}'
            r = requests.get(url)
            data_DIVIDENDS = r.json()

            url = f'https://www.alphavantage.co/query?function=SPLITS&symbol={ticker}&apikey={apikey}'
            r = requests.get(url)
            data_SPLITS = r.json()

            splits_sorted = sorted(data_SPLITS['data'], key=lambda x: x['effective_date'], reverse=True)
            # Construir factors inversos acumulats (de més recent a més antic)
            split_factors = []
            cumulative_factor = 1.0
            for s in splits_sorted:
                date = pd.to_datetime(s['effective_date']).date()
                factor = float(s['split_factor'])
                cumulative_factor /= factor  # invers del factor
                split_factors.append((date, cumulative_factor))


            if not data_INCOME_STATEMENT or "Error Message" in data_INCOME_STATEMENT:
                print(f"No s'han trobat dades per {ticker}")
                continue

            for q_Reports in data_INCOME_STATEMENT['quarterlyReports']:
            
                date_obj = pd.to_datetime(q_Reports['fiscalDateEnding']).date()

                # Sumar tots els dividends que pertanyen al trimestre actual
                dividend_sum = calculo_dividendos(data_DIVIDENDS, split_factors, date_obj)

                revenue = float(q_Reports['totalRevenue']) if q_Reports['totalRevenue'] not in [None, 'None'] else None
                net_income = float(q_Reports['netIncome']) if q_Reports['netIncome'] not in [None, 'None'] else None

                # Com que Alpha Vantage no dona 'Close' ni 'Dividends', podem posar-ho a None/0
                Fundamental.objects.update_or_create(
                    stock=stock,
                    date=date_obj,
                    defaults={
                        'price': None,         # O pots buscar-ho amb yfinance si vols complementar
                        'dividend': dividend_sum,
                        'revenue': revenue,
                        'net_income': net_income,
                    }
                )

            print(f"{ticker} processat correctament.")
        except Exception as e:
            print(f"Error amb {ticker}: {e}")


def calculo_dividendos(data_DIVIDENDS, split_factors, date_obj):
    def get_quarter_end(date):
        """Donada una data, retorna la data de final de trimestre"""
        y = date.year
        if date.month <= 3: return datetime(y, 3, 31).date()
        elif date.month <= 6: return datetime(y, 6, 30).date()
        elif date.month <= 9: return datetime(y, 9, 30).date()
        else: return datetime(y, 12, 31).date()



    # Sumar dividends ajustats
    dividend_sum = 0.0
    for d in data_DIVIDENDS['data']:
        ex_date = pd.to_datetime(d['ex_dividend_date']).date()
        factor = 1.0
        for split in split_factors:
            if ex_date <= split[0]:
                factor = split[1]
                break
        if get_quarter_end(ex_date) == date_obj:
            amount = float(d['amount'])
            adjusted_amount = amount #* factor 
            dividend_sum += adjusted_amount

    return dividend_sum if dividend_sum > 0 else 0