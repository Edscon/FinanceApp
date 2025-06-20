from django.db import models

from django.db import models

class Stock(models.Model):
    ticker = models.CharField(max_length=10, unique=True)
    name = models.CharField(max_length=100)
    sector = models.CharField(max_length=50)

    def __str__(self):
        return f"{self.ticker} - {self.name}"

class Fundamental(models.Model):
    stock = models.ForeignKey(Stock, on_delete=models.CASCADE)
    date = models.DateField()

    price = models.FloatField(null=True, blank=True)  # Preu de l'acció en aquesta data
    market_cap = models.FloatField(null=True, blank=True)         # Capitalització de mercat

    revenue = models.FloatField(null=True, blank=True)
    net_income = models.FloatField(null=True, blank=True)
    pe_ratio = models.FloatField(null=True, blank=True)
    roe = models.FloatField(null=True, blank=True)
    dividend = models.FloatField(null=True, blank=True)

    ebit = models.FloatField(null=True, blank=True)  # Earnings Before Interest and Taxes
    ebitda = models.FloatField(null=True, blank=True)  # Earnings Before Interest

    earnings_per_share = models.FloatField(null=True, blank=True)
    debt_to_equity = models.FloatField(null=True, blank=True)
    current_ratio = models.FloatField(null=True, blank=True)
    profit_margin = models.FloatField(null=True, blank=True)
    operating_margin = models.FloatField(null=True, blank=True)
    return_on_assets = models.FloatField(null=True, blank=True)
    price_to_book = models.FloatField(null=True, blank=True)
    beta = models.FloatField(null=True, blank=True)

    class Meta:
        unique_together = ('stock', 'date')
        ordering = ['-date']