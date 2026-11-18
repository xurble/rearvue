"""
Django settings for the rearvue project.

Overview: https://docs.djangoproject.com/en/stable/topics/settings/
Reference: https://docs.djangoproject.com/en/stable/ref/settings/
"""

# Build paths inside the project like this: os.path.join(BASE_DIR, ...)
import os
import sys

from . import settings_server

BASE_DIR = os.path.dirname(os.path.dirname(__file__))

# Detect if running locally
RUNNING_LOCAL = 'runserver' in sys.argv


# Quick-start development settings — unsuitable for production.
# https://docs.djangoproject.com/en/stable/howto/deployment/checklist/

# SECURITY WARNING: keep the secret key used in production secret!
SECRET_KEY = settings_server.SECRET_KEY

# SECURITY WARNING: don't run with debug turned on in production!
DEBUG = settings_server.DEBUG

LOG_LOCATION = settings_server.LOG_LOCATION

DATA_STORE = settings_server.DATA_STORE

DATABASES = settings_server.DATABASES
CONN_HEALTH_CHECKS = getattr(settings_server, "CONN_HEALTH_CHECKS", False)

# for the sqlite people :)
DATABASES["default"]["NAME"] = DATABASES["default"]["NAME"].replace("__BASE_DIR__", BASE_DIR)

FLICKR_KEY =    settings_server.FLICKR_KEY
FLICKR_SECRET = settings_server.FLICKR_SECRET

INSTAGRAM_KEY =    settings_server.INSTAGRAM_KEY
INSTAGRAM_SECRET = settings_server.INSTAGRAM_SECRET

# Instagram API with Instagram Login (OAuth). Optional override if redirect cannot be derived from the domain.
INSTAGRAM_REDIRECT_URI = getattr(settings_server, "INSTAGRAM_REDIRECT_URI", None)
INSTAGRAM_GRAPH_API_VERSION = getattr(settings_server, "INSTAGRAM_GRAPH_API_VERSION", "v22.0")
INSTAGRAM_OAUTH_SCOPES = getattr(
    settings_server,
    "INSTAGRAM_OAUTH_SCOPES",
    "instagram_business_basic",
)

# Facebook Graph API settings for Instagram
FACEBOOK_ACCESS_TOKEN = getattr(settings_server, 'FACEBOOK_ACCESS_TOKEN', None)


DEFAULT_DOMAIN_PROTOCOL = settings_server.DEFAULT_DOMAIN_PROTOCOL # http or https

ALLOWED_HOSTS = settings_server.ALLOWED_HOSTS

# Application definition

INSTALLED_APPS = (
    'django.contrib.admin',
    'django.contrib.auth',
    'django.contrib.contenttypes',
    'django.contrib.sessions',
    'django.contrib.messages',
    'django.contrib.staticfiles',
    'rvsite',
    'rvmcp',
    'rvadmin',
    'feeds',
)

# The agent-facing MCP server is opt-in. Browser clients additionally require
# their exact Origin value to appear in MCP_ALLOWED_ORIGINS.
MCP_ENABLED = getattr(settings_server, "MCP_ENABLED", False)
MCP_ALLOWED_ORIGINS = getattr(settings_server, "MCP_ALLOWED_ORIGINS", [])
MCP_ALLOWED_HOSTS = getattr(settings_server, "MCP_ALLOWED_HOSTS", ALLOWED_HOSTS)
MCP_MAX_PAGE_SIZE = getattr(settings_server, "MCP_MAX_PAGE_SIZE", 100)
MCP_DEFAULT_PAGE_SIZE = getattr(settings_server, "MCP_DEFAULT_PAGE_SIZE", 50)
MCP_MAX_BULK_ITEMS = getattr(settings_server, "MCP_MAX_BULK_ITEMS", 100)
MCP_MAX_RAW_DATA_BYTES = getattr(settings_server, "MCP_MAX_RAW_DATA_BYTES", 256 * 1024)
MCP_MAX_REQUEST_BODY_BYTES = getattr(
    settings_server, "MCP_MAX_REQUEST_BODY_BYTES", 3 * 1024 * 1024
)
MCP_IDEMPOTENCY_TTL_SECONDS = getattr(
    settings_server, "MCP_IDEMPOTENCY_TTL_SECONDS", 24 * 60 * 60
)
MCP_GENERATED_ROOT = getattr(
    settings_server, "MCP_GENERATED_ROOT", os.path.join(DATA_STORE, "mcp-generated")
)
MCP_MAX_JOB_ATTEMPTS = getattr(settings_server, "MCP_MAX_JOB_ATTEMPTS", 3)
MCP_JOB_LEASE_SECONDS = getattr(settings_server, "MCP_JOB_LEASE_SECONDS", 60)
MCP_JOB_RETRY_BASE_SECONDS = getattr(settings_server, "MCP_JOB_RETRY_BASE_SECONDS", 5)
MCP_JOB_RESULT_MAX_BYTES = getattr(settings_server, "MCP_JOB_RESULT_MAX_BYTES", 256 * 1024)
MCP_DESTRUCTIVE_PREVIEW_TTL_SECONDS = getattr(
    settings_server, "MCP_DESTRUCTIVE_PREVIEW_TTL_SECONDS", 5 * 60
)
MCP_MAX_DESTRUCTIVE_RECORDS = getattr(settings_server, "MCP_MAX_DESTRUCTIVE_RECORDS", 100)
MCP_MAX_MEDIA_BYTES = getattr(settings_server, "MCP_MAX_MEDIA_BYTES", 25 * 1024 * 1024)
MCP_MAX_IMAGE_PIXELS = getattr(settings_server, "MCP_MAX_IMAGE_PIXELS", 40_000_000)
MCP_MAX_LINK_RESPONSE_BYTES = getattr(settings_server, "MCP_MAX_LINK_RESPONSE_BYTES", 1024 * 1024)
MCP_MAX_ARCHIVE_BYTES = getattr(settings_server, "MCP_MAX_ARCHIVE_BYTES", 2 * 1024 * 1024)
MCP_MAX_ARCHIVE_RECORDS = getattr(settings_server, "MCP_MAX_ARCHIVE_RECORDS", 10_000)
MCP_ARTIFACT_TTL_SECONDS = getattr(settings_server, "MCP_ARTIFACT_TTL_SECONDS", 24 * 60 * 60)
MCP_EXPORT_SNAPSHOT_TTL_SECONDS = getattr(
    settings_server, "MCP_EXPORT_SNAPSHOT_TTL_SECONDS", 24 * 60 * 60
)
MCP_MAX_EXPORT_SNAPSHOT_RECORDS = getattr(
    settings_server, "MCP_MAX_EXPORT_SNAPSHOT_RECORDS", 10_000
)

MIDDLEWARE = (
    'django.middleware.security.SecurityMiddleware',
    'django.contrib.sessions.middleware.SessionMiddleware',
    'django.middleware.common.CommonMiddleware',
    'django.middleware.csrf.CsrfViewMiddleware',
    'django.contrib.auth.middleware.AuthenticationMiddleware',
    'django.contrib.messages.middleware.MessageMiddleware',
    'django.middleware.clickjacking.XFrameOptionsMiddleware',
)

# Secure-by-default production settings. Deployments behind a TLS-terminating
# proxy can override these values in settings_server as needed.
SECURE_SSL_REDIRECT = getattr(settings_server, "SECURE_SSL_REDIRECT", not DEBUG)
SESSION_COOKIE_SECURE = getattr(settings_server, "SESSION_COOKIE_SECURE", not DEBUG)
CSRF_COOKIE_SECURE = getattr(settings_server, "CSRF_COOKIE_SECURE", not DEBUG)
SECURE_HSTS_SECONDS = getattr(settings_server, "SECURE_HSTS_SECONDS", 31536000 if not DEBUG else 0)
SECURE_HSTS_INCLUDE_SUBDOMAINS = getattr(
    settings_server, "SECURE_HSTS_INCLUDE_SUBDOMAINS", not DEBUG
)
SECURE_HSTS_PRELOAD = getattr(settings_server, "SECURE_HSTS_PRELOAD", False)
SECURE_PROXY_SSL_HEADER = getattr(settings_server, "SECURE_PROXY_SSL_HEADER", None)

ROOT_URLCONF = 'rearvue.urls'

WSGI_APPLICATION = 'rearvue.wsgi.application'
ASGI_APPLICATION = 'rearvue.asgi.application'

DEFAULT_AUTO_FIELD = 'django.db.models.AutoField'

# Internationalization
# https://docs.djangoproject.com/en/stable/topics/i18n/

LANGUAGE_CODE = 'en-us'

TIME_ZONE = 'UTC'

USE_I18N = True

USE_L10N = True

USE_TZ = True

FEEDS_USER_AGENT = "RearVue"
FEEDS_SERVER = settings_server.FEEDS_SERVER
FEEDS_CLOUDFLARE_WORKER = None

# Static files (CSS, JavaScript, Images)
# https://docs.djangoproject.com/en/stable/howto/static-files/

STATIC_URL = '/static/'
MEDIA_URL = '/media/'


STATIC_ROOT = settings_server.STATIC_ROOT
MEDIA_ROOT = settings_server.MEDIA_ROOT


# List of finder classes that know how to find static files in
# various locations.
STATICFILES_FINDERS = (
    'django.contrib.staticfiles.finders.FileSystemFinder',
    'django.contrib.staticfiles.finders.AppDirectoriesFinder',
#    'django.contrib.staticfiles.finders.DefaultStorageFinder',
)


TEMPLATES = [
        {
            'BACKEND': 'django.template.backends.django.DjangoTemplates',
            'DIRS': [os.path.join(BASE_DIR, "templates")],
            'APP_DIRS': True,
            'OPTIONS': {
                'context_processors': [
                    'django.template.context_processors.debug',
                    'django.template.context_processors.request',
                    'django.contrib.auth.context_processors.auth',
                    'django.contrib.messages.context_processors.messages',
                ],
                'debug' : DEBUG,
            },
        },
    ]

LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "root": {
        "level": "INFO",
        "handlers": ["console" if RUNNING_LOCAL else "file"]
    },
    "handlers": {
        "file": {
            "level": "INFO",
            'class': 'logging.handlers.RotatingFileHandler',
            "filename": LOG_LOCATION,
            'maxBytes': 1024*1024*5,  # 5 MB
            'backupCount': 5,
            "formatter": "colored" if RUNNING_LOCAL else "app",
        },
        "console": {
            "level": "INFO",
            "class": "logging.StreamHandler",
            "formatter": "colored",
        },
    },
    "loggers": {
        "django": {
            "handlers": [],
            "level": "INFO",
            "propagate": True
        },
    },
    "formatters": {
        "app": {
            "format": (
                u"%(asctime)s [%(levelname)-8s] "
                "(%(module)s.%(funcName)s) %(message)s"
            ),
            "datefmt": "%Y-%m-%d %H:%M:%S",
        },
        "colored": {
            "()": "colorlog.ColoredFormatter",
            "format": "%(log_color)s%(asctime)s [%(levelname)-8s] %(message)s",
            "datefmt": "%Y-%m-%d %H:%M:%S",
            "log_colors": {
                "DEBUG": "cyan",
                "INFO": "green",
                "WARNING": "yellow",
                "ERROR": "red",
                "CRITICAL": "red",
            },
        },
    },
}
