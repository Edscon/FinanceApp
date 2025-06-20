from django import forms

class StockFilterForm(forms.Form):
    sector = forms.CharField(required=False, label="Sector")
    min_market_cap = forms.FloatField(required=False, label="Capitalització mínima")
    max_market_cap = forms.FloatField(required=False, label="Capitalització màxima")