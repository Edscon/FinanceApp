from django.apps import AppConfig


class BacktestsConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'backtests'
    verbose_name = 'Backtests'

    def ready(self):
        from . import jobs  # noqa: F401  registra los handlers de trabajos (no arranca hilos)
