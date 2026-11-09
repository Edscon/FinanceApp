from django.core.management.base import BaseCommand

from market.services import load_presets


class Command(BaseCommand):
    help = 'Crea o actualiza los universos predefinidos (sin descargar precios).'

    def handle(self, *args, **options):
        for universe in load_presets():
            self.stdout.write(f'{universe.name}: {universe.members.count()} valores')
