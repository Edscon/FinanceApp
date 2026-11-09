from django.contrib import admin

from .models import DailyBar


@admin.register(DailyBar)
class DailyBarAdmin(admin.ModelAdmin):
    list_display = ('company', 'date', 'adj_close', 'volume')
    list_select_related = ('company',)
    date_hierarchy = 'date'
    search_fields = ('company__ticker',)
    raw_id_fields = ('company',)
