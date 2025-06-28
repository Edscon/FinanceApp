# screener/management/commands/import_fmp.py

from django.core.management.base import BaseCommand
from django.conf import settings
from datetime import datetime
import requests
from screener.models import Company, FundamentalsQuarter
from collections import defaultdict


def get_data_response(url):
    try:
        res = requests.get(url)
        res.raise_for_status()

        data = res.json()
        if not data or not isinstance(data, list):
            print("No hi ha dades disponibles o format desconegut.")
            return

        return data
    except requests.exceptions.HTTPError as e: 
        print("Error HTTP:", res.text)
        return
    except Exception as e:
        print("Error de connexió:", str(e))
        return
    
def update_or_crate_company(ticker):
    company, created = Company.objects.get_or_create(ticker=ticker)
    if created or not company.name:
        url = f"https://financialmodelingprep.com/stable/search-symbol?query={ticker}&apikey={settings.FMP_API_KEY}"
        data_company = get_data_response(url)
        if data_company:
            info = data_company[0]
            company.name = info.get('name') or company.name
            company.exchange = info.get('exchange') or company.exchange
            company.currency = info.get('currency') or company.currency
            company.save()
            print("Info bàsica de l'empresa actualitzada.")
    return company

def update_or_crate_income_statment(ticker, company, q):
        url = f"https://financialmodelingprep.com/stable/income-statement?symbol={ticker}&period=Q{q}&apikey={settings.FMP_API_KEY}"
        data = get_data_response(url)
        created, updated = 0, 0
        for item in data:
            try:
                period_end = datetime.strptime(item.get('date'), '%Y-%m-%d').date()
                filing_date = datetime.strptime(item.get('filingDate'), '%Y-%m-%d').date() if item.get('filingDate') else None
                accepted_date = datetime.strptime(item.get('acceptedDate'), '%Y-%m-%d %H:%M:%S') if item.get('acceptedDate') else None
            except Exception as e:
                print("Error parsejant dates:", e)
                continue

            defaults = {
                'period_end': period_end,
                'fiscal_year': item.get('fiscalYear'),
                'period': item.get('period'),
                'reported_currency': item.get('reportedCurrency'),
                'cik': item.get('cik'),
                'filing_date': filing_date,
                'accepted_date': accepted_date,
                # Income-statement fields
                'revenue': item.get('revenue'),
                'cost_of_revenue': item.get('costOfRevenue'),
                'gross_profit': item.get('grossProfit'),
                'research_and_development_expenses': item.get('researchAndDevelopmentExpenses'),
                'general_and_administrative_expenses': item.get('generalAndAdministrativeExpenses'),
                'selling_and_marketing_expenses': item.get('sellingAndMarketingExpenses'),
                'selling_general_and_administrative_expenses': item.get('sellingGeneralAndAdministrativeExpenses'),
                'other_expenses': item.get('otherExpenses'),
                'operating_expenses': item.get('operatingExpenses'),
                'cost_and_expenses': item.get('costAndExpenses'),
                'net_interest_income': item.get('netInterestIncome'),
                'interest_income': item.get('interestIncome'),
                'interest_expense': item.get('interestExpense'),
                'depreciation_and_amortization': item.get('depreciationAndAmortization'),
                'ebitda': item.get('ebitda'),
                'ebit': item.get('ebit'),
                'non_operating_income_excl_interest': item.get('nonOperatingIncomeExcludingInterest'),
                'operating_income': item.get('operatingIncome'),
                'total_other_income_expenses_net': item.get('totalOtherIncomeExpensesNet'),
                'income_before_tax': item.get('incomeBeforeTax'),
                'income_tax_expense': item.get('incomeTaxExpense'),
                'net_income_from_continuing_ops': item.get('netIncomeFromContinuingOperations'),
                'net_income_from_discontinued_ops': item.get('netIncomeFromDiscontinuedOperations'),
                'other_adjustments_to_net_income': item.get('otherAdjustmentsToNetIncome'),
                'net_income': item.get('netIncome'),
                'net_income_deductions': item.get('netIncomeDeductions'),
                'bottom_line_net_income': item.get('bottomLineNetIncome'),
                'eps': item.get('eps'),
                'eps_diluted': item.get('epsDiluted'),
                'weighted_average_shs_out': item.get('weightedAverageShsOut'),
                'weighted_average_shs_out_dil': item.get('weightedAverageShsOutDil'),
            }

            obj, was_created = FundamentalsQuarter.objects.update_or_create(
                company=company,
                fiscal_year=item.get('fiscalYear'),
                period=item.get('period'),
                defaults=defaults
            )

            if was_created:
                created += 1
            else:
                updated += 1

        print(f"Importació completada: {created} registres creats, {updated} actualitzats.")

def update_or_crate_dividends(ticker, company):
    url = f"https://financialmodelingprep.com/stable/dividends?symbol={ticker}&apikey={settings.FMP_API_KEY}"
    data = get_data_response(url)

    dividends_by_quarter = defaultdict(float)  # Clau: (any, trimestre) -> suma dividends
    if data:
        for item in data:
            date_str = item.get('date')
            dividend = item.get('adjDividend', 0)
            if not date_str:
                continue
            date = datetime.strptime(date_str, "%Y-%m-%d")
            quarter = (date.month - 1) // 3 + 1

            key = (date.year, quarter)
            dividends_by_quarter[key] += dividend
        
        for divident in dividends_by_quarter.items():
            year, quarter = divident[0]
            adj_divident = divident[1]
            try:
                obj = FundamentalsQuarter.objects.get(company=company, fiscal_year=year, period=f"Q{quarter}")
                obj.adj_dividend = adj_divident
                obj.save()
            except FundamentalsQuarter.DoesNotExist:
                FundamentalsQuarter.objects.create(
                    company=company,
                    fiscal_year=year,
                    reported_currency=company.currency,
                    period=f"Q{quarter}",
                    adj_dividend=adj_divident
                )
                continue
    

class Command(BaseCommand):
    help = 'Importa dades de FMP per a un ticker'

    def add_arguments(self, parser):
        parser.add_argument('tickers', nargs='*', type=str, help='Lista de símbolos')

    def handle(self, *args, **options):
        tickers = options['tickers']
        if not tickers:
            # Si no pasas tickers, usa una lista fija
            tickers = ['AAPL', 'MSFT', 'TSLA']
        
        for ticker in tickers:
            self.stdout.write(f'Procesant dades per {ticker}...')

            # 1) Obtenir o crear Company
            company = update_or_crate_company(ticker)

            # 2) Income statement trimestral
            for q in range(1, 5):
                update_or_crate_income_statment(ticker, company, q)

            # 3) Dividends
            update_or_crate_dividends(ticker, company)

