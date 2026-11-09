from django.db import models


class Job(models.Model):
    """Trabajo en segundo plano (importar datos, ejecutar un backtest...). Ver core/jobs.py."""

    class Status(models.TextChoices):
        QUEUED = 'queued', 'En cola'
        RUNNING = 'running', 'En curso'
        SUCCEEDED = 'succeeded', 'Completado'
        FAILED = 'failed', 'Fallido'
        CANCELLED = 'cancelled', 'Cancelado'

    ACTIVE = (Status.QUEUED, Status.RUNNING)

    kind = models.CharField(max_length=40)
    label = models.CharField(max_length=200, blank=True)
    status = models.CharField(max_length=12, choices=Status.choices, default=Status.QUEUED, db_index=True)
    payload = models.JSONField(default=dict, blank=True)
    progress = models.FloatField(default=0.0)
    message = models.CharField(max_length=300, blank=True)
    result = models.JSONField(default=dict, blank=True)
    error = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    started_at = models.DateTimeField(null=True, blank=True)
    finished_at = models.DateTimeField(null=True, blank=True)
    heartbeat_at = models.DateTimeField(null=True, blank=True)
    worker_id = models.CharField(max_length=80, blank=True)
    cancel_requested = models.BooleanField(default=False)

    class Meta:
        ordering = ['-created_at']

    def __str__(self):
        return f'{self.kind} #{self.pk} ({self.status})'

    @property
    def is_active(self):
        return self.status in self.ACTIVE

    @property
    def percent(self):
        return round(100 * (self.progress or 0))
