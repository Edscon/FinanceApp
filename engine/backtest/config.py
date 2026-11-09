"""Configuración de un backtest. Los valores por defecto se fijaron ANTES de ver datos reales,
a partir de la literatura (ver docs/ESTRATEGIAS.md): cambiarlos después de mirar resultados es
sobreajuste."""
from __future__ import annotations

import dataclasses
import datetime as dt
from dataclasses import dataclass, field
from enum import Enum

SCHEMA_VERSION = 1


class Execution(str, Enum):
    NEXT_CLOSE = 'next_close'  # señal al cierre de t, ejecución al cierre de t+1 (por defecto)
    NEXT_OPEN = 'next_open'  # ejecución a la apertura de t+1 (cierre si falta la apertura)


@dataclass(frozen=True)
class CostModel:
    commission_bps: float = 1.0  # sobre el nocional negociado, por lado
    slippage_bps: float = 5.0  # se paga en el precio: compra a P(1+s), venta a P(1-s)
    borrow_bps_annual: float = 50.0  # préstamo de los cortos, sobre su valor

    def __post_init__(self):
        for name in ('commission_bps', 'slippage_bps', 'borrow_bps_annual'):
            if getattr(self, name) < 0:
                raise ValueError(f'{name} no puede ser negativo.')

    def scaled(self, factor: float) -> 'CostModel':
        return CostModel(self.commission_bps * factor, self.slippage_bps * factor, self.borrow_bps_annual * factor)


@dataclass(frozen=True)
class ReversalRules:
    """Reversión relativa intra-sector (Da/Liu/Schaumburg 2014, Hameed & Mian 2015)."""

    lookback: int = 5  # sesiones del "gap": cuánto se separó cada valor de su grupo
    rebalance: int = 5  # cada cuántas sesiones se recalculan las posiciones
    vol_window: int = 60  # volatilidad para normalizar el gap
    liquidity_window: int = 20  # mediana de volumen en $
    gross: float = 1.0  # exposición bruta objetivo (largos + cortos) / capital
    max_weight: float = 0.05  # tope por valor
    min_gap: float = 0.0  # opcional: solo valores cuyo gap supere estas sigmas
    min_price: float = 5.0
    min_dollar_volume: float = 0.0  # mediana de precio x volumen; 0 = sin filtro
    min_group_size: int = 3
    no_trade_band: float = 0.001  # no operar diferencias menores que esta fracción del capital

    def __post_init__(self):
        if self.lookback < 1 or self.rebalance < 1:
            raise ValueError('lookback y rebalance deben ser >= 1.')
        if self.vol_window < 10:
            raise ValueError('vol_window debe ser >= 10.')
        if not 0 < self.gross <= 4:
            raise ValueError('gross debe estar en (0, 4].')
        if not 0 < self.max_weight <= 1:
            raise ValueError('max_weight debe estar en (0, 1].')
        if self.min_group_size < 3:
            raise ValueError('min_group_size debe ser >= 3.')

    def warmup(self) -> int:
        return max(self.lookback, self.vol_window, self.liquidity_window) + 1


@dataclass(frozen=True)
class PairsRules:
    """Pares cointegrados en walk-forward: se eligen con la formación y se operan después."""

    formation: int = 504
    trading: int = 126
    max_pairs: int = 10
    gross_per_pair: float = 0.2
    entry_z: float = 2.0
    exit_z: float = 0.5
    stop_z: float = 4.0
    max_hold_hl: float = 3.0  # time stop en múltiplos del half-life
    require_cross: bool = True  # entrar solo cuando |z| cruza el umbral (no si ya estaba fuera)
    beta_min: float = 0.25
    beta_max: float = 4.0
    min_corr: float = 0.70
    max_p_adj: float = 0.05
    min_half_life: float = 2.0
    max_half_life: float = 30.0
    use_groups: bool = True  # solo pares del mismo grupo

    def __post_init__(self):
        if self.formation < 60 or self.trading < 5:
            raise ValueError('formation >= 60 y trading >= 5.')
        if not 0 <= self.exit_z < self.entry_z < self.stop_z:
            raise ValueError('Se exige 0 <= exit_z < entry_z < stop_z.')
        if self.max_pairs < 1 or not 0 < self.gross_per_pair <= 1:
            raise ValueError('max_pairs >= 1 y gross_per_pair en (0, 1].')
        if not 0 < self.beta_min < self.beta_max:
            raise ValueError('Se exige 0 < beta_min < beta_max.')


RULES = {'reversal': ReversalRules, 'pairs': PairsRules}
STRATEGY_NAMES = {'reversal': 'Reversión intra-sector', 'pairs': 'Pares cointegrados (walk-forward)'}


def _known(cls, values: dict) -> dict:
    names = {f.name for f in dataclasses.fields(cls)}
    return {k: v for k, v in (values or {}).items() if k in names}


@dataclass(frozen=True)
class BacktestConfig:
    strategy: str
    rules: ReversalRules | PairsRules
    costs: CostModel = field(default_factory=CostModel)
    execution: Execution = Execution.NEXT_CLOSE
    capital: float = 100_000.0
    start: dt.date | None = None
    end: dt.date | None = None

    def __post_init__(self):
        if self.strategy not in RULES:
            raise ValueError(f'Estrategia desconocida: {self.strategy}')
        if not isinstance(self.rules, RULES[self.strategy]):
            raise ValueError(f'Las reglas no corresponden a la estrategia {self.strategy}.')
        if self.capital <= 0:
            raise ValueError('El capital debe ser positivo.')
        if self.start and self.end and self.start > self.end:
            raise ValueError('La fecha inicial debe ser anterior a la final.')
        object.__setattr__(self, 'execution', Execution(self.execution))

    @classmethod
    def default(cls, strategy: str, **changes) -> 'BacktestConfig':
        return cls(strategy=strategy, rules=RULES[strategy](), **changes)

    def to_dict(self) -> dict:
        return {
            'schema': SCHEMA_VERSION,
            'strategy': self.strategy,
            'rules': dataclasses.asdict(self.rules),
            'costs': dataclasses.asdict(self.costs),
            'execution': self.execution.value,
            'capital': self.capital,
            'start': self.start.isoformat() if self.start else None,
            'end': self.end.isoformat() if self.end else None,
        }

    @classmethod
    def from_dict(cls, data: dict) -> 'BacktestConfig':
        strategy = data['strategy']
        rules_cls = RULES[strategy]
        return cls(
            strategy=strategy,
            rules=rules_cls(**_known(rules_cls, data.get('rules'))),
            costs=CostModel(**_known(CostModel, data.get('costs'))),
            execution=Execution(data.get('execution', Execution.NEXT_CLOSE.value)),
            capital=float(data.get('capital', 100_000.0)),
            start=dt.date.fromisoformat(data['start']) if data.get('start') else None,
            end=dt.date.fromisoformat(data['end']) if data.get('end') else None,
        )

    def replace(self, **changes) -> 'BacktestConfig':
        return dataclasses.replace(self, **changes)

    def with_rules(self, **changes) -> 'BacktestConfig':
        return dataclasses.replace(self, rules=dataclasses.replace(self.rules, **changes))
