import datetime as dt

from django import forms

from engine.backtest.config import STRATEGY_NAMES, BacktestConfig, CostModel, Execution, PairsRules, ReversalRules
from market.models import Universe

DATE = forms.DateInput(attrs={'type': 'date'}, format='%Y-%m-%d')
REV, PAIRS, COSTS = ReversalRules(), PairsRules(), CostModel()


def num(label, initial, help_text='', step='any', minimum=None, integer=False, required=True):
    field_cls = forms.IntegerField if integer else forms.FloatField
    attrs = {'step': 1 if integer else step}
    return field_cls(label=label, initial=initial, help_text=help_text, required=required, min_value=minimum,
                     widget=forms.NumberInput(attrs=attrs))


class BacktestForm(forms.Form):
    """Los valores iniciales son los por defecto del motor, fijados antes de ver datos reales."""

    REVERSAL_FIELDS = ['lookback', 'rebalance', 'vol_window', 'gross', 'max_weight', 'min_gap', 'min_price', 'min_dollar_volume_m']
    PAIRS_FIELDS = ['formation', 'trading', 'max_pairs', 'gross_per_pair', 'entry_z', 'exit_z', 'stop_z', 'max_hold_hl',
                    'min_corr', 'max_p_adj', 'max_half_life', 'require_cross']

    strategy = forms.ChoiceField(label='Estrategia', choices=list(STRATEGY_NAMES.items()), initial='reversal')
    name = forms.CharField(label='Nombre', required=False, help_text='Opcional.')
    universe = forms.ModelChoiceField(label='Universo', queryset=Universe.objects.filter(members__instrument__n_bars__gt=0).distinct(),
                                      empty_label=None, help_text='Solo se comparan valores del mismo grupo del universo.')
    start = forms.DateField(label='Desde', required=False, widget=DATE, help_text='Vacío = desde que hay historia suficiente.')
    end = forms.DateField(label='Hasta', required=False, widget=DATE, help_text='Vacío = hasta el último dato.')
    capital = num('Capital inicial ($)', 100_000, minimum=1000)
    execution = forms.ChoiceField(
        label='Ejecución', initial=Execution.NEXT_CLOSE.value,
        choices=[(Execution.NEXT_CLOSE.value, 'Al cierre de la sesión siguiente (recomendado)'),
                 (Execution.NEXT_OPEN.value, 'A la apertura de la sesión siguiente')],
        help_text='La señal se calcula al cierre; nunca se ejecuta al mismo precio que la generó.',
    )
    commission_bps = num('Comisión (pb por lado)', COSTS.commission_bps, minimum=0)
    slippage_bps = num('Deslizamiento (pb por lado)', COSTS.slippage_bps, 'Diferencial y precio peor que el teórico.', minimum=0)
    borrow_bps_annual = num('Préstamo de cortos (pb/año)', COSTS.borrow_bps_annual, minimum=0)

    lookback = num('Sesiones del gap', REV.lookback, 'Cuánto se separó cada valor de su grupo (5 = una semana).', integer=True, minimum=1)
    rebalance = num('Rebalanceo cada (sesiones)', REV.rebalance, integer=True, minimum=1)
    vol_window = num('Ventana de volatilidad', REV.vol_window, integer=True, minimum=10)
    gross = num('Exposición bruta', REV.gross, 'Largos + cortos sobre el capital (1 = 50 % largo y 50 % corto).', minimum=0.1)
    max_weight = num('Peso máximo por valor', REV.max_weight, minimum=0.005)
    min_gap = num('Gap mínimo (σ)', REV.min_gap, 'Solo valores separados al menos estas sigmas; 0 = todos.', minimum=0)
    min_price = num('Precio mínimo ($)', REV.min_price, minimum=0)
    min_dollar_volume_m = num('Volumen mínimo (millones de $)', 0.0, 'Mediana diaria; 0 = sin filtro.', minimum=0)

    formation = num('Formación (sesiones)', PAIRS.formation, 'Historia con la que se eligen los pares.', integer=True, minimum=60)
    trading = num('Operativa (sesiones)', PAIRS.trading, 'Cuánto se operan antes de volver a elegir.', integer=True, minimum=5)
    max_pairs = num('Pares máximos', PAIRS.max_pairs, integer=True, minimum=1)
    gross_per_pair = num('Bruto por par', PAIRS.gross_per_pair, minimum=0.01)
    entry_z = num('Entrada |z|', PAIRS.entry_z, minimum=0.1)
    exit_z = num('Salida |z|', PAIRS.exit_z, minimum=0)
    stop_z = num('Stop |z|', PAIRS.stop_z, minimum=0.2)
    max_hold_hl = num('Time stop (× half-life)', PAIRS.max_hold_hl, minimum=0.5)
    min_corr = num('Correlación mínima', PAIRS.min_corr)
    max_p_adj = num('p ajustado máximo', PAIRS.max_p_adj, minimum=0.0001)
    max_half_life = num('Half-life máximo (sesiones)', PAIRS.max_half_life, minimum=3)
    require_cross = forms.BooleanField(label='Entrar solo al cruzar el umbral', initial=PAIRS.require_cross, required=False)

    def clean(self):
        cleaned = super().clean()
        if self.errors:
            return cleaned
        try:
            cleaned['config'] = self.to_config(cleaned)
        except ValueError as exc:
            raise forms.ValidationError(str(exc)) from None
        return cleaned

    @staticmethod
    def to_config(c) -> BacktestConfig:
        costs = CostModel(c['commission_bps'], c['slippage_bps'], c['borrow_bps_annual'])
        if c['strategy'] == 'reversal':
            rules = ReversalRules(
                lookback=c['lookback'], rebalance=c['rebalance'], vol_window=c['vol_window'], gross=c['gross'],
                max_weight=c['max_weight'], min_gap=c['min_gap'], min_price=c['min_price'],
                min_dollar_volume=c['min_dollar_volume_m'] * 1e6,
            )
        else:
            rules = PairsRules(
                formation=c['formation'], trading=c['trading'], max_pairs=c['max_pairs'], gross_per_pair=c['gross_per_pair'],
                entry_z=c['entry_z'], exit_z=c['exit_z'], stop_z=c['stop_z'], max_hold_hl=c['max_hold_hl'],
                min_corr=c['min_corr'], max_p_adj=c['max_p_adj'], max_half_life=c['max_half_life'],
                require_cross=c['require_cross'],
            )
        return BacktestConfig(strategy=c['strategy'], rules=rules, costs=costs, execution=c['execution'],
                              capital=c['capital'], start=c.get('start'), end=c.get('end'))

    @staticmethod
    def initial_from(config: BacktestConfig, universe=None, name='') -> dict:
        initial = {
            'strategy': config.strategy, 'name': name, 'capital': config.capital, 'execution': config.execution.value,
            'start': config.start, 'end': config.end, 'commission_bps': config.costs.commission_bps,
            'slippage_bps': config.costs.slippage_bps, 'borrow_bps_annual': config.costs.borrow_bps_annual,
        }
        if universe is not None:
            initial['universe'] = universe.pk
        rules = config.rules
        for key in BacktestForm.REVERSAL_FIELDS + BacktestForm.PAIRS_FIELDS:
            if hasattr(rules, key):
                initial[key] = getattr(rules, key)
        if hasattr(rules, 'min_dollar_volume'):
            initial['min_dollar_volume_m'] = rules.min_dollar_volume / 1e6
        return initial

    def section(self, names):
        return [self[name] for name in names]

    def common_fields(self):
        return self.section(['name', 'universe', 'start', 'end', 'capital', 'execution'])

    def cost_fields(self):
        return self.section(['commission_bps', 'slippage_bps', 'borrow_bps_annual'])

    def reversal_fields(self):
        return self.section(self.REVERSAL_FIELDS)

    def pairs_fields(self):
        return self.section(self.PAIRS_FIELDS)


class ScannerForm(forms.Form):
    universe = forms.ModelChoiceField(label='Universo', queryset=Universe.objects.filter(members__instrument__n_bars__gt=0).distinct(),
                                      empty_label=None)
    as_of = forms.DateField(label='A fecha de', required=False, widget=DATE, help_text='Vacío = último dato.')
    lookback = num('Ventana (sesiones)', 504, integer=True, minimum=120)
    min_corr = num('Correlación mínima', 0.70)
    max_p = num('p ajustado máximo', 0.05, minimum=0.0001)
    entry_z = num('Gap a partir de |z|', 2.0, minimum=0.1)

    def clean_as_of(self):
        value = self.cleaned_data.get('as_of')
        if value and value > dt.date.today():
            raise forms.ValidationError('La fecha no puede ser futura.')
        return value
