"""
Django settings — HomeManager backend.
"""

import os
from pathlib import Path
from datetime import timedelta
from decimal import Decimal

from django.core.exceptions import ImproperlyConfigured

# ── Paths ────────────────────────────────────────────────────────────────
BASE_DIR = Path(__file__).resolve().parent.parent  # hmbe/

# Configuration is read from the process environment only — Compose builds it from
# the root ``.env`` on the local machine, and the deploy host exports it. There is
# deliberately no second ``.env`` inside ``hmbe/``: that file let a process point at
# a different Postgres/Redis than the product does, which is where "but it worked
# locally" bugs came from. There is also no host virtualenv — no ``hmbe/venv`` to run
# ``manage.py`` with, and none should come back. **Local backend == the containers in the
# compose stack** (dev override included); ``02-project-setup.md`` §5.1 is the single
# runbook.

# ── Secret key ───────────────────────────────────────────────────────────
SECRET_KEY = os.getenv(
    'DJANGO_SECRET_KEY',
    'django-insecure-dev-key-replace-in-production',
)

# ── Debug / environment ─────────────────────────────────────────────────
DEBUG = os.getenv('DEBUG', 'False').lower() in ('true', '1', 'yes')
ALLOWED_HOSTS = os.getenv('ALLOWED_HOSTS', 'localhost,127.0.0.1').split(',')

# ── Application definition ──────────────────────────────────────────────
INSTALLED_APPS = [
    # Django built-in
    'django.contrib.admin',
    'django.contrib.auth',
    'django.contrib.contenttypes',
    'django.contrib.sessions',
    'django.contrib.messages',
    'django.contrib.staticfiles',

    # Third-party
    'rest_framework',
    'rest_framework_simplejwt',
    'rest_framework_simplejwt.token_blacklist',  # refresh-token revocation (logout)
    'corsheaders',
    'channels',
    'django_extensions',

    # Local apps
    'apps.core',        # health check, tenant middleware, shared API plumbing
    'apps.society',
    'apps.users',
    'apps.units',
    'apps.notices',
    'apps.chat',
    'apps.invoices',
    'apps.payments',
    'apps.dashboard',   # resident + treasurer aggregation endpoints
]

MIDDLEWARE = [
    'django.middleware.security.SecurityMiddleware',
    'corsheaders.middleware.CorsMiddleware',
    'django.contrib.sessions.middleware.SessionMiddleware',
    'django.middleware.common.CommonMiddleware',
    'django.middleware.csrf.CsrfViewMiddleware',
    'django.contrib.auth.middleware.AuthenticationMiddleware',
    'django.contrib.messages.middleware.MessageMiddleware',
    'django.middleware.clickjacking.XFrameOptionsMiddleware',
    # Must sit after AuthenticationMiddleware — it reads request.user.
    'apps.core.middleware.TenantContextMiddleware',
]

ROOT_URLCONF = 'backend.urls'

TEMPLATES = [
    {
        'BACKEND': 'django.template.backends.django.DjangoTemplates',
        'DIRS': [],
        'APP_DIRS': True,
        'OPTIONS': {
            'context_processors': [
                'django.template.context_processors.debug',
                'django.template.context_processors.request',
                'django.contrib.auth.context_processors.auth',
                'django.contrib.messages.context_processors.messages',
            ],
        },
    },
]

WSGI_APPLICATION = 'backend.wsgi.application'

# ── ASGI / Channels ─────────────────────────────────────────────────────
ASGI_APPLICATION = 'backend.asgi.application'

# ── Database ────────────────────────────────────────────────────────────
# Postgres in the compose stack is the only supported database. A silent SQLite
# fallback used to let a process with no DATABASE_URL come up against a different
# (empty) database — every tenant query then "worked" and returned nothing. Failing
# at import is the honest behaviour, and the message says where to run instead.
DATABASE_URL = os.getenv('DATABASE_URL', '')
if not DATABASE_URL.startswith('postgresql://'):
    raise ImproperlyConfigured(
        'DATABASE_URL must be a postgresql:// URL. The backend runs in Docker: start the '
        'stack and run commands through the container —\n'
        '  docker compose -f docker-compose.yml -f docker-compose.dev.yml up -d --build\n'
        '  docker compose -f docker-compose.yml -f docker-compose.dev.yml exec backend '
        'python manage.py <command>\n'
        'Compose sets DATABASE_URL from the root .env; see 02-project-setup.md §4.1 and §5.1.'
    )

import dj_database_url  # noqa: E402

DATABASES = {'default': dj_database_url.parse(DATABASE_URL)}

# ── Password validation ─────────────────────────────────────────────────
AUTH_PASSWORD_VALIDATORS = [
    {'NAME': 'django.contrib.auth.password_validation.UserAttributeSimilarityValidator'},
    {'NAME': 'django.contrib.auth.password_validation.MinimumLengthValidator'},
    {'NAME': 'django.contrib.auth.password_validation.CommonPasswordValidator'},
    {'NAME': 'django.contrib.auth.password_validation.NumericPasswordValidator'},
]

# ── Internationalisation ────────────────────────────────────────────────
LANGUAGE_CODE = 'en-us'
TIME_ZONE = 'Asia/Kolkata'
USE_I18N = True
USE_TZ = True

# ── Static / Media files ────────────────────────────────────────────────
STATIC_URL = '/static/'
STATIC_ROOT = BASE_DIR / 'staticfiles'
MEDIA_URL = '/media/'
# Must stay inside BASE_DIR — docker-compose mounts ./hmbe/media to /app/media.
MEDIA_ROOT = BASE_DIR / 'media'

# ── Default primary key ─────────────────────────────────────────────────
DEFAULT_AUTO_FIELD = 'django.db.models.BigAutoField'

# ── Custom user model ───────────────────────────────────────────────────
AUTH_USER_MODEL = 'users.CustomUser'

# ── DRF / JWT config ────────────────────────────────────────────────────
REST_FRAMEWORK = {
    'DEFAULT_AUTHENTICATION_CLASSES': (
        # Tenant-aware wrapper around simplejwt — see apps/core/authentication.py
        'apps.core.authentication.TenantJWTAuthentication',
    ),
    'DEFAULT_PERMISSION_CLASSES': (
        'rest_framework.permissions.IsAuthenticated',
    ),
    'DEFAULT_PAGINATION_CLASS': 'apps.core.pagination.ApiPagination',
    'PAGE_SIZE': 20,
    'DEFAULT_RENDERER_CLASSES': (
        'rest_framework.renderers.JSONRenderer',
    ),
    'DEFAULT_PARSER_CLASSES': (
        'rest_framework.parsers.JSONParser',
        'rest_framework.parsers.MultiPartParser',
        'rest_framework.parsers.FormParser',
    ),
    'EXCEPTION_HANDLER': 'apps.core.exceptions.custom_exception_handler',
}

# Lifetimes are read as SECONDS from the environment (doc 02 §gap 6).
SIMPLE_JWT = {
    'ACCESS_TOKEN_LIFETIME': timedelta(
        seconds=int(os.getenv('JWT_ACCESS_TOKEN_LIFETIME', '900'))
    ),
    'REFRESH_TOKEN_LIFETIME': timedelta(
        seconds=int(os.getenv('JWT_REFRESH_TOKEN_LIFETIME', '604800'))
    ),
    'ROTATE_REFRESH_TOKENS': True,
    'BLACKLIST_AFTER_ROTATION': True,
    'UPDATE_LAST_LOGIN': True,
    'ALGORITHM': 'HS256',
    # Tokens are signed with JWT_SECRET so that the JWT signing key can be
    # rotated without invalidating DJANGO_SECRET_KEY (which also protects
    # sessions, signatures and salted hashes). Falls back to SECRET_KEY so a
    # missing var can never produce an unsigned-token footgun.
    'SIGNING_KEY': os.getenv('JWT_SECRET') or SECRET_KEY,
    'AUTH_HEADER_TYPES': ('Bearer',),
    'AUTH_HEADER_NAME': 'HTTP_AUTHORIZATION',
    'USER_ID_FIELD': 'id',
    'USER_ID_CLAIM': 'user_id',
}

# ── CORS / CSRF origins ─────────────────────────────────────────────────
CORS_ALLOWED_ORIGINS = [
    o.strip()
    for o in os.getenv('CORS_ALLOWED_ORIGINS', 'http://localhost:25105').split(',')
    if o.strip()
]
# Django 4+ requires the scheme-qualified origin for unsafe (POST) requests that
# carry a Host header it did not generate itself. Empty in dev (the admin posts to
# its own origin); on the deploy host set it to the public origin, e.g.
# ``https://app.homemanager.in``.
CSRF_TRUSTED_ORIGINS = [
    o.strip()
    for o in os.getenv('CSRF_TRUSTED_ORIGINS', '').split(',')
    if o.strip()
]

# ── Behind the shared edge proxy ────────────────────────────────────────
# On a deploy host this app is one service among several: the machine's nginx is
# the only public listener, terminates TLS, and forwards here over plain HTTP on
# loopback (doc 09 §2). Without this block Django believes it is serving plain
# HTTP at ``127.0.0.1:8000`` — so admin redirects, CSRF checks and every absolute
# URL it builds are wrong, silently.
#
# ``BEHIND_PROXY`` stays False in development: there is no edge between Vite and
# the container, and enabling it locally would make dev lie about the request.
# The HTTPS→HTTP redirect is deliberately left to nginx, not done twice.
BEHIND_PROXY = os.getenv('BEHIND_PROXY', 'False').lower() in ('true', '1', 'yes')

# How many addresses the trusted infrastructure puts in X-Forwarded-For to the
# right of the real client. The edge is configured to OVERWRITE the header with
# ``$remote_addr`` (deploy/nginx/snippets/proxy.conf), so one edge == 1. Raising
# it is correct only for a real chain (CDN → edge). Used by
# ``apps.core.client_ip.get_client_ip``; a client-supplied header is never read.
PROXY_HOPS = int(os.getenv('PROXY_HOPS', '1')) if BEHIND_PROXY else 0

if BEHIND_PROXY:
    USE_X_FORWARDED_HOST = True
    # Only trusted because the edge sets it; the app itself is not on a public port.
    SECURE_PROXY_SSL_HEADER = ('HTTP_X_FORWARDED_PROTO', 'https')
    SESSION_COOKIE_SECURE = True
    CSRF_COOKIE_SECURE = True
    SECURE_HSTS_SECONDS = int(os.getenv('SECURE_HSTS_SECONDS', '31536000'))
    SECURE_HSTS_INCLUDE_SUBDOMAINS = True
    SECURE_CONTENT_TYPE_NOSNIFF = True
    X_FRAME_OPTIONS = 'DENY'
elif int(os.getenv('SECURE_HSTS_SECONDS', '0')):
    raise ImproperlyConfigured(
        'SECURE_HSTS_SECONDS is set but BEHIND_PROXY is False — HSTS only makes '
        'sense once TLS is terminated upstream. Set BEHIND_PROXY=True (deploy host) '
        'or drop the HSTS value.'
    )

# ── Redis (cache + Channels broker) ─────────────────────────────────────
REDIS_URL = os.getenv('REDIS_URL', 'redis://redis:6379/0')

CACHES = {
    'default': {
        'BACKEND': 'django.core.cache.backends.redis.RedisCache',
        'LOCATION': REDIS_URL,
    },
}

CHANNEL_LAYERS = {
    'default': {
        'BACKEND': 'channels_redis.core.RedisChannelLayer',
        'CONFIG': {
            'hosts': [REDIS_URL],
        },
    },
}

# ── OTP settings ────────────────────────────────────────────────────────
OTP_EXPIRY_SECONDS = int(os.getenv('OTP_EXPIRY_SECONDS', '300'))
OTP_MAX_ATTEMPTS = int(os.getenv('OTP_MAX_ATTEMPTS', '3'))
OTP_RESEND_COOLDOWN_SECONDS = int(os.getenv('OTP_RESEND_COOLDOWN_SECONDS', '60'))

# ── UPI (Phase 1: intent deep links, no status callback — doc 07) ───────
UPI_PAYEE_VPA = os.getenv('UPI_PAYEE_VPA', 'homemanager@razorpay')
UPI_PAYEE_NAME = os.getenv('UPI_PAYEE_NAME', 'HomeManager Platform')
UPI_INTENT_TTL_SECONDS = int(os.getenv('UPI_INTENT_TTL_SECONDS', '900'))

# ── Subscription pricing (Phase 1: Starter flat rate) ───────────────────
STARTER_MONTHLY_FEE = Decimal(os.getenv('STARTER_MONTHLY_FEE', '1999.00'))
MAINTENANCE_FLAT_RATE = Decimal(os.getenv('MAINTENANCE_FLAT_RATE', '1999.00'))
NON_OCCUPANCY_SURCHARGE_RATE = Decimal(os.getenv('NON_OCCUPANCY_SURCHARGE_RATE', '0.10'))
INVOICE_DUE_DAY = int(os.getenv('INVOICE_DUE_DAY', '15'))

# ── Logging ─────────────────────────────────────────────────────────────
LOGGING = {
    'version': 1,
    'disable_existing_loggers': False,
    'formatters': {
        'simple': {
            'format': '[{asctime}] {levelname} {name} {message}',
            'style': '{',
        },
    },
    'handlers': {
        'console': {'class': 'logging.StreamHandler', 'formatter': 'simple'},
    },
    'root': {'handlers': ['console'], 'level': os.getenv('LOG_LEVEL', 'INFO')},
    'loggers': {
        'apps': {'handlers': ['console'], 'level': os.getenv('LOG_LEVEL', 'INFO'), 'propagate': False},
        'django.request': {'handlers': ['console'], 'level': 'ERROR', 'propagate': False},
    },
}
