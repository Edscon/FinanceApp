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
