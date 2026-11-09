"""Datos de mercado para el backtest: matrices fechas x tickers de precios ajustados."""
from __future__ import annotations

import hashlib
from dataclasses import dataclass, field

import numpy as np
import pandas as pd


@dataclass
class MarketData:
    """Cierres (obligatorios), aperturas y volumen (opcionales) ajustados, más el grupo de cada
    ticker y un benchmark opcional (p. ej. SPY), alineados con ``close.index``."""

    close: pd.DataFrame
    open: pd.DataFrame | None = None
    volume: pd.DataFrame | None = None
    groups: dict[str, str] = field(default_factory=dict)
    benchmark: pd.Series | None = None
    benchmark_name: str = ''

    def __post_init__(self):
        if self.close is None or self.close.empty:
            raise ValueError('No hay precios de cierre.')
        close = self.close.copy()
        close.index = pd.DatetimeIndex(close.index)
        close.columns = [str(c) for c in close.columns]
        if close.index.has_duplicates:
            raise ValueError('Hay fechas duplicadas en los cierres.')
        # Orden determinista: por fecha y por ticker.
        self.close = close.sort_index().sort_index(axis=1).astype(float)
        if (self.close <= 0).any().any():
            raise ValueError('Hay precios de cierre <= 0.')
        self.open = self._align(self.open)
        self.volume = self._align(self.volume)
        if self.benchmark is not None:
            bench = self.benchmark.copy()
            bench.index = pd.DatetimeIndex(bench.index)
            self.benchmark = bench.astype(float).reindex(self.close.index)
        self.groups = {str(k): str(v) for k, v in (self.groups or {}).items() if v}

    def _align(self, frame):
        if frame is None:
            return None
        frame = frame.copy()
        frame.index = pd.DatetimeIndex(frame.index)
        frame.columns = [str(c) for c in frame.columns]
        return frame.reindex(index=self.close.index, columns=self.close.columns).astype(float)

    @property
    def tickers(self) -> list[str]:
        return list(self.close.columns)

    @property
    def dates(self) -> pd.DatetimeIndex:
        return self.close.index

    def has_opens(self) -> bool:
        return self.open is not None and bool(np.isfinite(self.open.to_numpy()).any())

    def arrays(self):
        """Copias float de solo lectura (pandas 3 devuelve vistas con copy-on-write)."""

        def frozen(frame):
            if frame is None:
                return None
            array = np.array(frame.to_numpy(dtype=float), dtype=float, copy=True)
            array.setflags(write=False)
            return array

        return frozen(self.close), frozen(self.open), frozen(self.volume)

    def fingerprint(self) -> str:
        """Huella de los datos: si se reimportan (los ajustes por dividendos cambian el pasado),
        cambia y los resultados guardados dejan de ser comparables."""
        digest = hashlib.sha256()
        digest.update('|'.join(self.tickers).encode())
        digest.update(np.asarray(self.close.index.asi8).tobytes())
        digest.update(np.round(np.nan_to_num(self.close.to_numpy(dtype=float), nan=-1.0), 6).tobytes())
        if self.open is not None:
            digest.update(np.round(np.nan_to_num(self.open.to_numpy(dtype=float), nan=-1.0), 6).tobytes())
        digest.update(repr(sorted(self.groups.items())).encode())
        return digest.hexdigest()[:24]
