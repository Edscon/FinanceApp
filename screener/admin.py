from django.contrib import admin
from .models import Company, FundamentalsQuarter

admin.site.register(Company)

@admin.register(FundamentalsQuarter)
class FundamentalsQuarterAdmin(admin.ModelAdmin):
    ordering = ['company', '-fiscal_year', '-period']
    list_display = ['display_name','period_end']

    @admin.display(description='Company – Year – Period')
    def display_name(self, obj):
        return f"{obj.company.ticker} – {obj.fiscal_year} – {obj.period}"