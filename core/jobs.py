"""Cola de trabajos en segundo plano respaldada por la base de datos.

- ``enqueue()`` crea el trabajo y, al confirmarse la transacción, ``kick()`` lo pone en marcha
  según ``settings.JOBS_MODE``: un hilo dentro del servidor (``thread``), en línea (``sync``,
  para tests) o nada (``external``: lo recoge ``manage.py run_worker``).
- El hilo NO se arranca en ``AppConfig.ready()``: con el autoreload de ``runserver`` habría dos
  procesos y dos workers. Se arranca bajo demanda, solo en el proceso que atiende peticiones.
- El *claim* es atómico (``UPDATE ... WHERE status='queued'``), porque SQLite ignora
  ``select_for_update``. Un trabajo cuyo latido lleva más de ``STALE_AFTER`` segundos sin
  actualizarse se da por muerto (p. ej. el servidor se reinició a mitad).
"""
from __future__ import annotations

import datetime as dt
import logging
import os
import socket
import threading
import time
import traceback

from django.conf import settings
from django.db import connection, transaction
from django.utils import timezone

from engine.backtest.result import jsonsafe

from .models import Job

log = logging.getLogger(__name__)

HANDLERS = {}
STALE_AFTER = 120  # segundos sin latido
_thread = None
_lock = threading.Lock()


class JobCancelled(Exception):
    """El usuario pidió cancelar el trabajo."""


def register(kind):
    """Decorador: ``@register('backtest') def handler(job, reporter) -> dict``."""

    def decorator(fn):
        HANDLERS[kind] = fn
        return fn

    return decorator


def enqueue(kind: str, payload: dict | None = None, label: str = '') -> Job:
    job = Job.objects.create(kind=kind, payload=jsonsafe(payload or {}), label=label[:200])
    if getattr(settings, 'JOBS_MODE', 'thread') == 'sync':
        kick()
    else:
        transaction.on_commit(kick)
    return job


def kick() -> None:
    mode = getattr(settings, 'JOBS_MODE', 'thread')
    if mode == 'sync':
        drain()
        return
    if mode == 'external':
        return
    global _thread
    with _lock:
        if _thread is None or not _thread.is_alive():
            _thread = threading.Thread(target=_thread_main, name='jobs-worker', daemon=True)
            _thread.start()


def _thread_main() -> None:
    try:
        drain()
    except Exception:  # pragma: no cover - el hilo nunca debe morir sin dejar rastro
        log.exception('El worker en hilo terminó con un error inesperado')
    finally:
        connection.close()  # en Windows, una conexión abierta bloquea el fichero de la BD


def worker_id() -> str:
    return f'{socket.gethostname()}-{os.getpid()}-{threading.get_ident()}'[:80]


def reap_stale(now=None) -> int:
    """Marca como fallidos los trabajos 'en curso' cuyo proceso ya no da señales de vida."""
    now = now or timezone.now()
    cutoff = now - dt.timedelta(seconds=STALE_AFTER)
    return Job.objects.filter(status=Job.Status.RUNNING, heartbeat_at__lt=cutoff).update(
        status=Job.Status.FAILED,
        finished_at=now,
        error='Interrumpido: el proceso que lo ejecutaba se detuvo (por ejemplo, al reiniciar el servidor).',
    )


def claim_next(worker: str) -> Job | None:
    candidates = Job.objects.filter(status=Job.Status.QUEUED).order_by('created_at', 'pk')
    for job_id in candidates.values_list('pk', flat=True)[:10]:
        now = timezone.now()
        claimed = Job.objects.filter(pk=job_id, status=Job.Status.QUEUED).update(
            status=Job.Status.RUNNING, started_at=now, heartbeat_at=now, worker_id=worker
        )
        if claimed:
            return Job.objects.get(pk=job_id)
    return None


def drain(worker: str | None = None, once: bool = False) -> int:
    """Ejecuta trabajos en cola hasta vaciarla. Devuelve cuántos ejecutó."""
    worker = worker or worker_id()
    reap_stale()
    done = 0
    while True:
        job = claim_next(worker)
        if job is None:
            return done
        run_job(job)
        done += 1
        if once:
            return done


class Reporter:
    """Lo que recibe cada handler para informar del progreso y comprobar la cancelación."""

    MIN_INTERVAL = 0.5  # segundos entre escrituras de progreso

    def __init__(self, job: Job):
        self.job = job
        self._last_write = 0.0

    def progress(self, fraction: float, message: str = '') -> None:
        now = time.monotonic()
        if now - self._last_write < self.MIN_INTERVAL and fraction < 1:
            return
        self._last_write = now
        Job.objects.filter(pk=self.job.pk).update(
            progress=max(0.0, min(1.0, float(fraction))),
            message=message[:300],
            heartbeat_at=timezone.now(),
        )
        if self.cancelled():
            raise JobCancelled()

    def cancelled(self) -> bool:
        return Job.objects.filter(pk=self.job.pk, cancel_requested=True).exists()


def _redact(text: str) -> str:
    key = getattr(settings, 'FMP_API_KEY', None)
    return text.replace(key, '***') if key else text


def run_job(job: Job) -> Job:
    handler = HANDLERS.get(job.kind)
    reporter = Reporter(job)
    try:
        if handler is None:
            raise RuntimeError(f'Tipo de trabajo desconocido: {job.kind}')
        result = handler(job, reporter) or {}
    except JobCancelled:
        Job.objects.filter(pk=job.pk).update(
            status=Job.Status.CANCELLED, finished_at=timezone.now(), message='Cancelado por el usuario'
        )
    except Exception:
        log.exception('Falló el trabajo %s', job)
        Job.objects.filter(pk=job.pk).update(
            status=Job.Status.FAILED,
            finished_at=timezone.now(),
            error=_redact(traceback.format_exc())[-6000:],
        )
    else:
        Job.objects.filter(pk=job.pk).update(
            status=Job.Status.SUCCEEDED,
            finished_at=timezone.now(),
            progress=1.0,
            result=jsonsafe(result),
            message=str(result.get('message', 'Completado'))[:300] if isinstance(result, dict) else 'Completado',
        )
    job.refresh_from_db()
    return job
