"""Worker externo: ejecuta los trabajos en cola (útil con JOBS_MODE=external)."""
import time

from django.core.management.base import BaseCommand

from core import jobs
from core.models import Job


class Command(BaseCommand):
    help = 'Ejecuta los trabajos en segundo plano (importaciones, backtests) hasta que lo pares con Ctrl+C.'

    def add_arguments(self, parser):
        parser.add_argument('--once', action='store_true', help='Ejecuta como mucho un trabajo y termina')
        parser.add_argument('--poll', type=float, default=1.0, help='Segundos entre comprobaciones de la cola')

    def handle(self, *args, **options):
        worker = jobs.worker_id()
        self.stdout.write(f'Worker {worker} esperando trabajos (Ctrl+C para parar)...')
        try:
            while True:
                done = jobs.drain(worker=worker, once=options['once'])
                if options['once']:
                    self.stdout.write(f'Ejecutados: {done}')
                    return
                time.sleep(options['poll'])
        except KeyboardInterrupt:
            interrupted = Job.objects.filter(status=Job.Status.RUNNING, worker_id=worker).update(
                status=Job.Status.FAILED, error='Interrumpido con Ctrl+C.'
            )
            self.stdout.write(f'Parado. Trabajos interrumpidos: {interrupted}')
