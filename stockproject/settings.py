"""Configuración de Django para FinanceApp (backtesting de gaps entre valores correlacionados).

Todo lo sensible o dependiente del entorno se lee de variables de entorno o del fichero .env
(ver .env.example). Pensado para uso local de un solo usuario.
"""
import os
from pathlib import Path

from django.core.exceptions import ImproperlyConfigured
from dotenv import load_dotenv

# Busca un .env en stockproject/ o en la raíz del repo.
load_dotenv()

BASE_DIR = Path(__file__).resolve().parent.parent


def _env_bool(name, default):
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in ('1', 'true', 'yes', 'on')


# En desarrollo (por defecto) DEBUG=True; en producción define DJANGO_DEBUG=False.
DEBUG = _env_bool('DJANGO_DEBUG', True)

SECRET_KEY = os.getenv('DJANGO_SECRET_KEY')
if not SECRET_KEY:
    if not DEBUG:
        raise ImproperlyConfigured('Define DJANGO_SECRET_KEY cuando DJANGO_DEBUG=False.')
    SECRET_KEY = 'django-insecure-solo-desarrollo-local'

ALLOWED_HOSTS = [h.strip() for h in os.getenv('DJANGO_ALLOWED_HOSTS', '').split(',') if h.strip()]

INSTALLED_APPS = [
    'django.contrib.admin',
    'django.contrib.auth',
    'django.contrib.contenttypes',
    'django.contrib.sessions',
    'django.contrib.messages',
    'django.contrib.staticfiles',
    'core',
    'market',
    'backtests',
]

MIDDLEWARE = [
    'django.middleware.security.SecurityMiddleware',
    'django.contrib.sessions.middleware.SessionMiddleware',
    'django.middleware.common.CommonMiddleware',
    'django.middleware.csrf.CsrfViewMiddleware',
    'django.contrib.auth.middleware.AuthenticationMiddleware',
    'django.contrib.messages.middleware.MessageMiddleware',
    'django.middleware.clickjacking.XFrameOptionsMiddleware',
]

ROOT_URLCONF = 'stockproject.urls'

TEMPLATES = [
    {
        'BACKEND': 'django.template.backends.django.DjangoTemplates',
        'DIRS': [BASE_DIR / 'templates'],
        'APP_DIRS': True,
        'OPTIONS': {
            'context_processors': [
                'django.template.context_processors.request',
                'django.contrib.auth.context_processors.auth',
                'django.contrib.messages.context_processors.messages',
                'core.context_processors.app_context',
            ],
        },
    },
]

WSGI_APPLICATION = 'stockproject.wsgi.application'

# SQLite en modo WAL: permite leer mientras el worker escribe. transaction_mode IMMEDIATE evita
# los "database is locked" al promocionar transacciones de lectura a escritura (Django >= 5.1).
DATABASES = {
    'default': {
        'ENGINE': 'django.db.backends.sqlite3',
        # DB_PATH permite apuntar a otro fichero (p. ej. para pruebas).
        'NAME': os.getenv('DB_PATH') or BASE_DIR / 'db.sqlite3',
        'OPTIONS': {
            'timeout': 20,
            'transaction_mode': 'IMMEDIATE',
            'init_command': 'PRAGMA journal_mode=WAL; PRAGMA synchronous=NORMAL;',
        },
    }
}

AUTH_PASSWORD_VALIDATORS = [
    {'NAME': 'django.contrib.auth.password_validation.UserAttributeSimilarityValidator'},
    {'NAME': 'django.contrib.auth.password_validation.MinimumLengthValidator'},
    {'NAME': 'django.contrib.auth.password_validation.CommonPasswordValidator'},
    {'NAME': 'django.contrib.auth.password_validation.NumericPasswordValidator'},
]

LANGUAGE_CODE = 'es'
TIME_ZONE = 'Europe/Madrid'
USE_I18N = True
USE_TZ = True

STATIC_URL = '/static/'
STATICFILES_DIRS = [BASE_DIR / 'static']

DEFAULT_AUTO_FIELD = 'django.db.models.BigAutoField'

# Trabajos en segundo plano (importaciones, backtests):
#   thread   -> un hilo dentro del propio servidor (por defecto; un solo comando para arrancar)
#   external -> los ejecuta `python manage.py run_worker` en otra terminal
#   sync     -> en línea, dentro de la petición (tests)
JOBS_MODE = os.getenv('JOBS_MODE', 'thread')

FMP_API_KEY = os.getenv('FMP_API_KEY')

LOGGING = {
    'version': 1,
    'disable_existing_loggers': False,
    'handlers': {'console': {'class': 'logging.StreamHandler'}},
    'loggers': {
        'core': {'handlers': ['console'], 'level': 'INFO'},
        'market': {'handlers': ['console'], 'level': 'INFO'},
        'backtests': {'handlers': ['console'], 'level': 'INFO'},
    },
}
