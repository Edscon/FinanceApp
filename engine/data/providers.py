"""Proveedores de precios diarios.

Todo lo que entra al sistema pasa por ``Bar``: ``parse_eod_records`` normaliza la
respuesta de FMP (o un CSV) y el resto del código no sabe de dónde vienen los
datos. Los precios son *ajustados* por splits y dividendos.

Nota sobre FMP: el endpoint ``historical-price-eod/dividend-adjusted`` está
documentado como OHLC ajustado por dividendos más volumen, pero los nombres
exactos de las claves JSON no se han podido contrastar con la API real (el host
de FMP no era accesible al desarrollarlo). Por eso el parser acepta tanto
``adjClose`` / ``adjOpen`` ... como ``close`` / ``open`` ... (este endpoint ya
devuelve precios ajustados con cualquiera de los dos nombres) y está probado con
ambos formatos. Comprueba una respuesta real antes de fiarte de los datos:

    python manage.py import_prices AAPL --from 2025-01-02 --to 2025-01-10
"""
from __future__ import annotations

import datetime as dt
import logging
import math
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path

import pandas as pd
import requests

log = logging.getLogger(__name__)


class ProviderError(Exception):
    """Fallo al obtener o interpretar datos de un proveedor.

    Los mensajes nunca incluyen la URL ni la clave de API.
    """


@dataclass(frozen=True)
class Bar:
    """Barra diaria con precios ajustados por splits y dividendos."""

    date: dt.date
    adj_open: float | None
    adj_high: float | None
    adj_low: float | None
    adj_close: float
    volume: int | None


@dataclass(frozen=True)
class CompanyProfile:
    name: str | None
    exchange: str | None
    currency: str | None
    sector: str | None
    industry: str | None
    market_cap: int | None


# --------------------------------------------------------------------------
# Parseo (puro: no hace red)
# --------------------------------------------------------------------------

def _num(value) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _scaled(value, factor: float) -> float | None:
    number = _num(value)
    return number * factor if number is not None else None


def _text(value) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _first(record: Mapping, *keys: str):
    for key in keys:
        value = record.get(key)
        if value is not None and value != '':
            return value
    return None


def _parse_date(value) -> dt.date | None:
    text = _text(value)
    if text is None:
        return None
    try:
        return dt.datetime.strptime(text[:10], '%Y-%m-%d').date()
    except ValueError:
        return None


def parse_eod_records(payload) -> list[Bar]:
    """Convierte registros diarios en barras ordenadas de la más antigua a la más reciente.

    Acepta una lista de registros o un objeto ``{"historical": [...]}`` (formato
    antiguo de FMP). Cada registro puede usar claves ``adj*`` o nombres planos.
    Se ignoran los registros sin fecha válida o sin cierre > 0; si una fecha se
    repite, gana el último registro.
    """
    if isinstance(payload, Mapping):
        if 'historical' not in payload:
            raise ProviderError('Respuesta inesperada: no contiene registros diarios.')
        payload = payload['historical']
    if not isinstance(payload, list):
        raise ProviderError('Respuesta inesperada: se esperaba una lista de registros.')

    bars: dict[dt.date, Bar] = {}
    skipped = 0
    for record in payload:
        if not isinstance(record, Mapping):
            skipped += 1
            continue
        day = _parse_date(record.get('date'))
        close = _num(_first(record, 'adjClose', 'close'))
        if day is None or close is None or close <= 0:
            skipped += 1
            continue
        volume = _num(record.get('volume'))
        bars[day] = Bar(
            date=day,
            adj_open=_num(_first(record, 'adjOpen', 'open')),
            adj_high=_num(_first(record, 'adjHigh', 'high')),
            adj_low=_num(_first(record, 'adjLow', 'low')),
            adj_close=close,
            volume=int(volume) if volume is not None and volume >= 0 else None,
        )
    if skipped:
        log.warning('Se ignoraron %d registros inválidos de %d.', skipped, len(payload))
    return [bars[day] for day in sorted(bars)]


def parse_profile(payload) -> CompanyProfile | None:
    """Perfil de empresa a partir de la respuesta de ``profile`` (lista con un objeto)."""
    if isinstance(payload, Mapping):
        payload = [payload]
    if not isinstance(payload, list) or not payload or not isinstance(payload[0], Mapping):
        return None
    record = payload[0]
    market_cap = _num(_first(record, 'marketCap', 'mktCap'))
    return CompanyProfile(
        name=_text(_first(record, 'companyName', 'name')),
        exchange=_text(_first(record, 'exchange', 'exchangeShortName')),
        currency=_text(record.get('currency')),
        sector=_text(record.get('sector')),
        industry=_text(record.get('industry')),
        market_cap=int(market_cap) if market_cap is not None else None,
    )


# --------------------------------------------------------------------------
# CSV
# --------------------------------------------------------------------------

def read_price_csv(path) -> list[Bar]:
    """Lee un CSV con columnas planas ``date`` y ``adj_close`` o ``close``.

    Acepta cabeceras tipo yfinance (``Date, Open, High, Low, Close, Adj Close,
    Volume``). Si hay ``adj_close`` y ``close``, open/high/low se reescalan con
    el mismo factor para que las cuatro columnas queden ajustadas por igual.
    Si solo hay ``close`` se asume que ya viene ajustado.
    """
    path = Path(path)
    try:
        frame = pd.read_csv(path)
    except (OSError, pd.errors.ParserError, pd.errors.EmptyDataError) as exc:
        raise ProviderError(f'{path.name}: no se pudo leer el CSV ({type(exc).__name__}).') from None

    frame.columns = [str(c).strip().lower().replace(' ', '_') for c in frame.columns]
    if 'date' not in frame.columns:
        raise ProviderError(f'{path.name}: falta la columna "date".')
    if 'adj_close' not in frame.columns and 'close' not in frame.columns:
        raise ProviderError(f'{path.name}: falta la columna "adj_close" o "close".')

    records = []
    for row in frame.to_dict('records'):
        close = _num(row.get('close'))
        adj_close = _num(row.get('adj_close'))
        if adj_close is None:
            adj_close = close
        factor = adj_close / close if (adj_close is not None and close) else 1.0
        records.append(
            {
                'date': row.get('date'),
                'adjClose': adj_close,
                'adjOpen': _scaled(row.get('open'), factor),
                'adjHigh': _scaled(row.get('high'), factor),
                'adjLow': _scaled(row.get('low'), factor),
                'volume': row.get('volume'),
            }
        )
    bars = parse_eod_records(records)
    if not bars and len(frame):
        raise ProviderError(
            f'{path.name}: ninguna fila válida (¿fechas YYYY-MM-DD y cierres mayores que 0?).'
        )
    return bars


# --------------------------------------------------------------------------
# Financial Modeling Prep
# --------------------------------------------------------------------------

class FMPProvider:
    """Cliente mínimo de Financial Modeling Prep (API ``stable``).

    Reintenta con espera exponencial ante 429, 5xx y errores de red. Los errores
    4xx (clave inválida, plan insuficiente, símbolo desconocido) no se reintentan.
    Nunca se escribe la URL ni la clave en un mensaje de error: ``requests``
    incluye la URL completa (con ``apikey=``) en sus excepciones, así que solo se
    usa el nombre de la excepción.
    """

    BASE_URL = 'https://financialmodelingprep.com/stable'
    ENDPOINT_EOD = 'historical-price-eod/dividend-adjusted'
    ENDPOINT_PROFILE = 'profile'

    def __init__(
        self,
        api_key: str | None,
        *,
        session=None,
        timeout: float = 30.0,
        max_retries: int = 3,
        backoff: float = 1.0,
        sleep: Callable[[float], None] = time.sleep,
        base_url: str | None = None,
    ):
        if not api_key:
            raise ProviderError('Falta FMP_API_KEY: defínela en el entorno o en el fichero .env.')
        self._api_key = api_key
        self._session = session or requests.Session()
        self._timeout = timeout
        self._max_retries = max_retries
        self._backoff = backoff
        self._sleep = sleep
        self._base_url = (base_url or self.BASE_URL).rstrip('/')

    def daily_bars(
        self, ticker: str, start: dt.date | None = None, end: dt.date | None = None
    ) -> list[Bar]:
        params = {'symbol': ticker}
        if start:
            params['from'] = start.isoformat()
        if end:
            params['to'] = end.isoformat()
        return parse_eod_records(self._get(self.ENDPOINT_EOD, params))

    def profile(self, ticker: str) -> CompanyProfile | None:
        return parse_profile(self._get(self.ENDPOINT_PROFILE, {'symbol': ticker}))

    # -- internos ----------------------------------------------------------

    def _redact(self, text: str) -> str:
        return text.replace(self._api_key, '***')

    def _get(self, path: str, params: Mapping[str, str]):
        url = f'{self._base_url}/{path}'
        query = {**params, 'apikey': self._api_key}
        last_error = 'sin respuesta'
        for attempt in range(self._max_retries + 1):
            try:
                response = self._session.get(url, params=query, timeout=self._timeout)
            except requests.RequestException as exc:
                last_error = f'error de red ({type(exc).__name__})'
            else:
                status = response.status_code
                if status == 200:
                    return self._payload(path, response)
                if status != 429 and status < 500:
                    raise ProviderError(f'{path}: HTTP {status} {self._detail(response)}'.rstrip())
                last_error = f'HTTP {status}'
            if attempt < self._max_retries:
                self._sleep(self._backoff * 2**attempt)
        raise ProviderError(f'{path}: {last_error} tras {self._max_retries + 1} intentos.')

    def _payload(self, path: str, response):
        try:
            payload = response.json()
        except ValueError:
            raise ProviderError(f'{path}: la respuesta no es JSON.') from None
        if isinstance(payload, Mapping):
            message = payload.get('Error Message') or payload.get('error')
            if message:
                raise ProviderError(f'{path}: {self._redact(str(message))[:200]}')
        return payload

    def _detail(self, response) -> str:
        try:
            body = response.json()
        except ValueError:
            text = getattr(response, 'text', '') or ''
        else:
            text = ''
            if isinstance(body, Mapping):
                text = body.get('Error Message') or body.get('error') or body.get('message') or ''
        return self._redact(str(text)).strip()[:160]


# --------------------------------------------------------------------------
# Yahoo Finance (yfinance)
# --------------------------------------------------------------------------

def _price_first(columns: pd.MultiIndex) -> bool:
    return 'Close' in set(columns.get_level_values(0))


def parse_yf_frame(frame: pd.DataFrame | None, tickers: list[str]) -> dict[str, list[Bar]]:
    """Convierte la respuesta de ``yf.download(..., auto_adjust=True)`` en barras por ticker.

    Acepta columnas MultiIndex (Precio, Ticker) o (Ticker, Precio) y columnas planas (un solo
    ticker). La fecha de cada barra es la de su marca de tiempo tal cual (la zona horaria de la
    bolsa); convertir antes a UTC desplazaría días en bolsas asiáticas. Se descartan las filas sin
    cierre (p. ej. días anteriores a la salida a bolsa de ese valor).
    """
    out: dict[str, list[Bar]] = {}
    if frame is None or len(frame) == 0:
        return out
    multi = isinstance(frame.columns, pd.MultiIndex)
    for ticker in tickers:
        if multi:
            if _price_first(frame.columns):
                if ticker not in set(frame.columns.get_level_values(1)):
                    continue
                sub = frame.xs(ticker, axis=1, level=1)
            else:
                if ticker not in set(frame.columns.get_level_values(0)):
                    continue
                sub = frame[ticker]
        else:
            if len(tickers) != 1:
                raise ProviderError('Respuesta de Yahoo sin columnas por ticker.')
            sub = frame
        if 'Close' not in sub.columns:
            continue
        sub = sub[sub['Close'].notna()]
        columns = {name: sub[name].to_numpy() if name in sub.columns else [None] * len(sub)
                   for name in ('Open', 'High', 'Low', 'Close', 'Volume')}
        bars = []
        for i, ts in enumerate(sub.index):
            close = _num(columns['Close'][i])
            if close is None or close <= 0:
                continue
            volume = _num(columns['Volume'][i])
            bars.append(Bar(
                date=ts.date() if hasattr(ts, 'date') else _parse_date(ts),
                adj_open=_num(columns['Open'][i]),
                adj_high=_num(columns['High'][i]),
                adj_low=_num(columns['Low'][i]),
                adj_close=close,
                volume=int(volume) if volume is not None and volume >= 0 else None,
            ))
        if bars:
            out[ticker] = sorted(bars, key=lambda bar: bar.date)
    return out


def _new_york_now() -> dt.datetime:
    from zoneinfo import ZoneInfo

    return dt.datetime.now(ZoneInfo('America/New_York'))


class YahooProvider:
    """Precios diarios ajustados de Yahoo Finance mediante ``yfinance`` (gratis, sin clave).

    - Descarga por lotes con pausa entre lotes y reintentos con espera exponencial (Yahoo limita
      la frecuencia de peticiones).
    - ``auto_adjust=True`` explícito: OHLC ajustados por splits y dividendos de forma coherente.
    - Si el mercado de Nueva York sigue abierto, se descarta la barra de hoy (está incompleta).
    - ``yfinance`` se importa solo al usarlo; ``download`` se puede inyectar en los tests.
    """

    name = 'yahoo'

    def __init__(self, *, chunk_size=20, pause=1.0, max_retries=3, backoff=2.0,
                 sleep: Callable[[float], None] = time.sleep, download=None, now=_new_york_now):
        self.chunk_size = chunk_size
        self.pause = pause
        self.max_retries = max_retries
        self.backoff = backoff
        self._sleep = sleep
        self._download_fn = download
        self._now = now

    def _download(self):
        if self._download_fn is not None:
            return self._download_fn
        import yfinance as yf

        return yf.download

    def daily_bars_many(self, tickers, start: dt.date | None = None, end: dt.date | None = None, progress=None):
        """Devuelve ``(barras por ticker, error por ticker)``. Un ticker fallido no detiene al resto."""
        tickers = list(dict.fromkeys(tickers))
        download = self._download()
        result: dict[str, list[Bar]] = {}
        errors: dict[str, str] = {}
        chunks = [tickers[i : i + self.chunk_size] for i in range(0, len(tickers), self.chunk_size)]
        for k, chunk in enumerate(chunks):
            frame, last_error = None, ''
            for attempt in range(self.max_retries + 1):
                try:
                    frame = download(
                        chunk,
                        start=(start or dt.date(2000, 1, 1)).isoformat(),
                        end=(end + dt.timedelta(days=1)).isoformat() if end else None,  # 'end' es exclusivo
                        interval='1d',
                        auto_adjust=True,
                        actions=False,
                        progress=False,
                        threads=True,
                    )
                    break
                except Exception as exc:  # yfinance lanza tipos variados (límite de peticiones, red...)
                    last_error = type(exc).__name__
                    frame = None
                    if attempt < self.max_retries:
                        self._sleep(self.backoff * 2**attempt)
            if frame is None:
                for ticker in chunk:
                    errors[ticker] = f'Yahoo no respondió tras {self.max_retries + 1} intentos ({last_error}).'
                continue
            parsed = parse_yf_frame(frame, chunk)
            for ticker in chunk:
                bars = self._drop_partial(parsed.get(ticker, []))
                if bars:
                    result[ticker] = bars
                else:
                    errors[ticker] = 'Yahoo no devolvió datos (¿ticker incorrecto o dejó de cotizar?).'
            if progress:
                progress((k + 1) / len(chunks), f'Descargados {min((k + 1) * self.chunk_size, len(tickers))} de {len(tickers)} valores')
            if k < len(chunks) - 1 and self.pause:
                self._sleep(self.pause)
        return result, errors

    def daily_bars(self, ticker: str, start: dt.date | None = None, end: dt.date | None = None) -> list[Bar]:
        bars, errors = self.daily_bars_many([ticker], start, end)
        if ticker not in bars:
            raise ProviderError(errors.get(ticker, 'sin datos'))
        return bars[ticker]

    def _drop_partial(self, bars: list[Bar]) -> list[Bar]:
        if not bars:
            return bars
        now = self._now()
        if bars[-1].date == now.date() and (now.hour, now.minute) < (16, 30):
            return bars[:-1]
        return bars
