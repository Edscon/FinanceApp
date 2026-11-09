from django.contrib import admin

from .models import BacktestRun, SelectionCache, Trade


@admin.register(BacktestRun)
class BacktestRunAdmin(admin.ModelAdmin):
    list_display = ('pk', 'name', 'strategy', 'universe_name', 'variant_label', 'created_at')
    list_filter = ('strategy',)
    exclude = ('daily', 'windows', 'attribution')


@admin.register(Trade)
class TradeAdmin(admin.ModelAdmin):
    list_display = ('run', 'y', 'x', 'entry_date', 'exit_date', 'exit_reason', 'net_pnl')
    list_filter = ('exit_reason',)


admin.site.register(SelectionCache)
