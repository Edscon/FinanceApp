from django.contrib import admin

from .models import DailyBar, Instrument, Universe, UniverseMember


@admin.register(Instrument)
class InstrumentAdmin(admin.ModelAdmin):
    list_display = ('ticker', 'name', 'sector', 'kind', 'source', 'first_date', 'last_date', 'n_bars')
    search_fields = ('ticker', 'name')
    list_filter = ('kind', 'source', 'sector')


@admin.register(DailyBar)
class DailyBarAdmin(admin.ModelAdmin):
    list_display = ('instrument', 'date', 'adj_close', 'volume')
    list_select_related = ('instrument',)
    date_hierarchy = 'date'
    raw_id_fields = ('instrument',)


class MemberInline(admin.TabularInline):
    model = UniverseMember
    raw_id_fields = ('instrument',)
    extra = 0


@admin.register(Universe)
class UniverseAdmin(admin.ModelAdmin):
    list_display = ('name', 'benchmark', 'is_preset')
    inlines = [MemberInline]
