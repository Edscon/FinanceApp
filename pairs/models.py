from django.db import models


class DailyBar(models.Model):
    """Barra diaria con precios ajustados por splits y dividendos.

    Los precios ajustados sirven para calcular retornos y spreads, pero NO son el
    precio al que se negoció: no valen para dimensionar órdenes en acciones.
    """

    company = models.ForeignKey(
        'screener.Company', on_delete=models.CASCADE, related_name='daily_bars'
    )
    date = models.DateField()
    adj_open = models.FloatField(null=True, blank=True)
    adj_high = models.FloatField(null=True, blank=True)
    adj_low = models.FloatField(null=True, blank=True)
    adj_close = models.FloatField()
    volume = models.BigIntegerField(null=True, blank=True)

    class Meta:
        ordering = ['company_id', 'date']
        constraints = [
            models.UniqueConstraint(fields=['company', 'date'], name='uniq_dailybar_company_date'),
        ]
        indexes = [models.Index(fields=['date'])]

    def __str__(self):
        return f'{self.company_id} {self.date} {self.adj_close}'
