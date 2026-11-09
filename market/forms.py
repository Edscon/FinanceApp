import datetime as dt

from django import forms

from .importers import PROVIDERS
from .models import Universe
from .services import parse_members, parse_tickers

DATE = forms.DateInput(attrs={'type': 'date'}, format='%Y-%m-%d')


class ImportForm(forms.Form):
    provider = forms.ChoiceField(
        label='Proveedor',
        choices=[('yahoo', f"{PROVIDERS['yahoo']} (gratis, sin clave)"), ('fmp', f"{PROVIDERS['fmp']} (requiere FMP_API_KEY)")],
        initial='yahoo',
    )
    universe = forms.ModelChoiceField(
        label='Universo', queryset=Universe.objects.all(), required=False,
        empty_label='— Ninguno: solo la lista de tickers —',
        help_text='Se importan todos sus valores y su benchmark.',
    )
    tickers = forms.CharField(
        label='Tickers sueltos', required=False, widget=forms.Textarea(attrs={'rows': 2, 'placeholder': 'AAPL, MSFT, SPY'}),
        help_text='Separados por comas o espacios. Se suman a los del universo.',
    )
    start = forms.DateField(label='Desde', initial=dt.date(2010, 1, 1), widget=DATE)
    end = forms.DateField(label='Hasta', required=False, widget=DATE, help_text='Vacío = hasta hoy.')

    def clean(self):
        cleaned = super().clean()
        tickers = parse_tickers(cleaned.get('tickers') or '')
        universe = cleaned.get('universe')
        if universe:
            tickers = list(dict.fromkeys(universe.tickers() + ([universe.benchmark] if universe.benchmark else []) + tickers))
        if not tickers:
            raise forms.ValidationError('Elige un universo o escribe al menos un ticker.')
        if cleaned.get('end') and cleaned.get('start') and cleaned['end'] < cleaned['start']:
            raise forms.ValidationError('«Hasta» debe ser posterior a «Desde».')
        if universe and universe.name.startswith('Demo'):
            raise forms.ValidationError('El universo de demostración es sintético: cárgalo con el botón de datos de demo.')
        cleaned['ticker_list'] = tickers
        return cleaned


class UniverseForm(forms.ModelForm):
    members = forms.CharField(
        label='Valores', widget=forms.Textarea(attrs={'rows': 14, 'placeholder': 'KO, Bebidas\nPEP, Bebidas\nXOM, Energía'}),
        help_text='Una línea por valor: TICKER, Grupo. Solo se comparan valores del mismo grupo.',
    )

    class Meta:
        model = Universe
        fields = ['name', 'description', 'benchmark']
        labels = {'name': 'Nombre', 'description': 'Descripción', 'benchmark': 'Benchmark'}
        help_texts = {'benchmark': 'Ticker de referencia para comparar (p. ej. SPY). Opcional.'}
        widgets = {'description': forms.Textarea(attrs={'rows': 2})}

    def clean_members(self):
        rows = parse_members(self.cleaned_data['members'])
        if len(rows) < 3:
            raise forms.ValidationError('Hacen falta al menos 3 valores.')
        return rows
