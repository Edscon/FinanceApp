from django.db import models


class Instrument(models.Model):
    """Un valor (acción, ETF o índice) con sus barras diarias."""

    class Source(models.TextChoices):
        NONE = '', 'Sin datos'
        YAHOO = 'yahoo', 'Yahoo Finance'
        FMP = 'fmp', 'FMP'
        CSV = 'csv', 'CSV'
        SYNTHETIC = 'synthetic', 'Sintético'

    ticker = models.CharField(max_length=24, unique=True)
    name = models.CharField(max_length=128, blank=True)
    sector = models.CharField(max_length=64, blank=True)
    industry = models.CharField(max_length=128, blank=True)
    kind = models.CharField(max_length=8, default='stock')  # stock | etf | index
    source = models.CharField(max_length=12, choices=Source.choices, blank=True)
    first_date = models.DateField(null=True, blank=True)
    last_date = models.DateField(null=True, blank=True)
    n_bars = models.PositiveIntegerField(default=0)
    quality = models.JSONField(default=list, blank=True)  # avisos de calidad de datos
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['ticker']

    def __str__(self):
        return self.ticker


class DailyBar(models.Model):
    """Barra diaria con precios AJUSTADOS por splits y dividendos (sirven para retornos, no para
    dimensionar órdenes reales)."""

    instrument = models.ForeignKey(Instrument, on_delete=models.CASCADE, related_name='bars')
    date = models.DateField()
    adj_open = models.FloatField(null=True, blank=True)
    adj_high = models.FloatField(null=True, blank=True)
    adj_low = models.FloatField(null=True, blank=True)
    adj_close = models.FloatField()
    volume = models.BigIntegerField(null=True, blank=True)

    class Meta:
        ordering = ['instrument_id', 'date']
        constraints = [models.UniqueConstraint(fields=['instrument', 'date'], name='uniq_bar_instrument_date')]
        indexes = [models.Index(fields=['date'])]

    def __str__(self):
        return f'{self.instrument_id} {self.date} {self.adj_close}'


class Universe(models.Model):
    """Conjunto de valores con un grupo por miembro (sector, industria...). Las estrategias solo
    comparan valores del mismo grupo."""

    name = models.CharField(max_length=120, unique=True)
    description = models.TextField(blank=True)
    benchmark = models.CharField(max_length=24, blank=True, default='SPY')
    is_preset = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-is_preset', 'name']

    def __str__(self):
        return self.name

    def tickers(self) -> list[str]:
        return list(self.members.order_by('instrument__ticker').values_list('instrument__ticker', flat=True))

    def groups(self) -> dict[str, str]:
        return {
            ticker: group or sector
            for ticker, group, sector in self.members.values_list('instrument__ticker', 'group', 'instrument__sector')
            if group or sector
        }


class UniverseMember(models.Model):
    universe = models.ForeignKey(Universe, on_delete=models.CASCADE, related_name='members')
    instrument = models.ForeignKey(Instrument, on_delete=models.CASCADE, related_name='memberships')
    group = models.CharField(max_length=64, blank=True)

    class Meta:
        ordering = ['group', 'instrument__ticker']
        constraints = [models.UniqueConstraint(fields=['universe', 'instrument'], name='uniq_universe_member')]
