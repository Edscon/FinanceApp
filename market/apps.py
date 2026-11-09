from django.apps import AppConfig


class MarketConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'market'
    verbose_name = 'Datos de mercado'

    def ready(self):
        from . import jobs  # noqa: F401  registra los handlers de trabajos (no arranca hilos)
