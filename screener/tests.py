from django.test import TestCase
from django.urls import reverse

from .models import Company


class ScreenerFilterTests(TestCase):
    """El filtro del screener consulta sector y capitalización de Company.

    Esos campos faltaban en el modelo y cualquier filtro daba FieldError.
    """

    def setUp(self):
        Company.objects.create(ticker='AAA', name='Alpha', sector='Technology', market_cap=2_000_000_000)
        Company.objects.create(ticker='BBB', name='Beta', sector='Energy', market_cap=500_000_000)
        Company.objects.create(ticker='CCC', name='Gamma')  # sin perfil importado

    def tickers(self, **params):
        response = self.client.get(reverse('screener'), params)
        self.assertEqual(response.status_code, 200)
        return sorted(company.ticker for company in response.context['stocks'])

    def test_without_filters_lists_everything(self):
        self.assertEqual(self.tickers(), ['AAA', 'BBB', 'CCC'])

    def test_filters_by_sector(self):
        self.assertEqual(self.tickers(sector='tech'), ['AAA'])

    def test_filters_by_market_cap_range(self):
        self.assertEqual(self.tickers(min_market_cap=1_000_000_000), ['AAA'])
        self.assertEqual(self.tickers(max_market_cap=1_000_000_000), ['BBB'])
        self.assertEqual(
            self.tickers(min_market_cap=100_000_000, max_market_cap=3_000_000_000), ['AAA', 'BBB']
        )
