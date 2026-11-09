from django.db import models

from engine.backtest.config import STRATEGY_NAMES, BacktestConfig


class BacktestRun(models.Model):
    """Un backtest (o una variante de robustez de otro, con ``parent``)."""

    name = models.CharField(max_length=200, blank=True)
    strategy = models.CharField(max_length=20)
    universe = models.ForeignKey('market.Universe', on_delete=models.SET_NULL, null=True, blank=True, related_name='runs')
    universe_name = models.CharField(max_length=120, blank=True)
    config = models.JSONField(default=dict)
    job = models.ForeignKey('core.Job', on_delete=models.SET_NULL, null=True, blank=True, related_name='+')
    parent = models.ForeignKey('self', on_delete=models.CASCADE, null=True, blank=True, related_name='variants')
    variant_key = models.CharField(max_length=20, blank=True)
    variant_label = models.CharField(max_length=80, blank=True)
    fingerprint = models.CharField(max_length=32, blank=True)
    engine_version = models.CharField(max_length=10, blank=True)
    selection_key = models.CharField(max_length=40, blank=True)
    metrics = models.JSONField(default=dict, blank=True)
    daily = models.JSONField(default=dict, blank=True)
    windows = models.JSONField(default=list, blank=True)
    attribution = models.JSONField(default=dict, blank=True)
    warnings = models.JSONField(default=list, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-created_at']

    def __str__(self):
        return self.name or f'Backtest #{self.pk}'

    @property
    def status(self):
        return self.job.status if self.job_id else 'failed'

    @property
    def is_done(self):
        return self.status == 'succeeded'

    @property
    def is_active(self):
        return self.status in ('queued', 'running')

    @property
    def strategy_label(self):
        return STRATEGY_NAMES.get(self.strategy, self.strategy)

    def config_obj(self) -> BacktestConfig:
        return BacktestConfig.from_dict(self.config)


class Trade(models.Model):
    """Operación completa de la estrategia de pares."""

    run = models.ForeignKey(BacktestRun, on_delete=models.CASCADE, related_name='trades')
    trade_id = models.CharField(max_length=80)
    window = models.IntegerField()
    y = models.CharField(max_length=24)
    x = models.CharField(max_length=24)
    direction = models.SmallIntegerField()
    beta = models.FloatField()
    half_life = models.FloatField()
    signal_date = models.DateField()
    entry_date = models.DateField()
    exit_signal_date = models.DateField()
    exit_date = models.DateField()
    exit_reason = models.CharField(max_length=20)
    entry_z = models.FloatField()
    exit_z = models.FloatField(null=True, blank=True)
    holding_sessions = models.IntegerField()
    entry_gross_notional = models.FloatField()
    gross_pnl = models.FloatField()
    commission = models.FloatField()
    slippage = models.FloatField()
    borrow = models.FloatField()
    net_pnl = models.FloatField()

    class Meta:
        ordering = ['entry_date', 'trade_id']
        indexes = [models.Index(fields=['run', 'entry_date'])]

    @property
    def costs(self):
        return self.commission + self.slippage + self.borrow

    @property
    def return_on_gross(self):
        return self.net_pnl / self.entry_gross_notional if self.entry_gross_notional else None


class SelectionCache(models.Model):
    """Selección de pares por ventana (la etapa cara), reutilizable entre variantes."""

    key = models.CharField(max_length=40, unique=True)
    payload = models.JSONField(default=list)
    created_at = models.DateTimeField(auto_now_add=True)
