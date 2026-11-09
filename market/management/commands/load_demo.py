from django.core.management.base import BaseCommand

from market.importers import load_demo


class Command(BaseCommand):
    help = 'Carga el mercado sintético de demostración (tickers DEMO-*), sin conexión a internet.'

    def handle(self, *args, **options):
        universe = load_demo()
        self.stdout.write(f'Universo «{universe.name}»: {universe.members.count()} valores sintéticos.')
