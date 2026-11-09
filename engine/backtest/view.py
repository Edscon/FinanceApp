"""Lo único que ve una estrategia: los datos hasta la sesión ``t`` incluida.

Los arrays son de solo lectura y están truncados en ``t``: mirar al futuro es imposible por
construcción, no por disciplina. Un test lo comprueba alterando los datos posteriores a ``t``.
"""
from __future__ import annotations

import numpy as np
import pandas as pd


class MarketView:
    __slots__ = ('_close', '_open', '_volume', 't', 'tickers', 'dates', 'groups', 'column')

    def __init__(self, close, open_, volume, dates: pd.DatetimeIndex, tickers: list[str], groups: dict, t: int):
        self._close = close
        self._open = open_
        self._volume = volume
        self.t = t
        self.tickers = tickers
        self.dates = dates
        self.groups = groups
        self.column = {ticker: i for i, ticker in enumerate(tickers)}

    @property
    def close(self) -> np.ndarray:
        """Cierres de las sesiones 0..t (filas) x tickers (columnas)."""
        return self._close[: self.t + 1]

    @property
    def open(self) -> np.ndarray | None:
        return None if self._open is None else self._open[: self.t + 1]

    @property
    def volume(self) -> np.ndarray | None:
        return None if self._volume is None else self._volume[: self.t + 1]

    @property
    def date(self):
        return self.dates[self.t]
