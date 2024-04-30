"""
Django settings for rearvue project.

For more information on this file, see
https://docs.djangoproject.com/en/1.6/topics/settings/

For the full list of settings and their values, see
https://docs.djangoproject.com/en/1.6/ref/settings/
"""

# Build paths inside the project like this: os.path.join(BASE_DIR, ...)
import os
import sys

from . import settings_server

BASE_DIR = os.path.dirname(os.path.dirname(__file__))
print(BASE_DIR)

# Detect if running locally
RUNNING_LOCAL = 'runserver' in sys.argv


# Quick-start development settings - unsuitable for production
# See https://docs.djangoproject.com/en/1.6/howto/deployment/checklist/

# SECURITY WARNING: keep the secret key used in production secret!
SECRET_KEY = settings_server.SECRET_KEY

# SECURITY WARNING: don't run with debug turned on in production!
DEBUG = settings_server.DEBUG

LOG_LOCATION = settings_server.LOG_LOCATION

DATA_STORE = settings_server.DATA_STORE

DATABASES = settings_server.DATABASES

# for the sqlite people :)
DATABASES["default"]["NAME"] = DATABASES["default"]["NAME"].replace("__BASE_DIR__", BASE_DIR)

FLICKR_KEY =    settings_server.FLICKR_KEY
FLICKR_SECRET = settings_server.FLICKR_SECRET

INSTAGRAM_KEY =    settings_server.INSTAGRAM_KEY
INSTAGRAM_SECRET = settings_server.INSTAGRAM_SECRET


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
    'rvadmin',
    'feeds',
)

MIDDLEWARE = (
    'django.contrib.sessions.middleware.SessionMiddleware',
    'django.middleware.common.CommonMiddleware',
    'django.middleware.csrf.CsrfViewMiddleware',
    'django.contrib.auth.middleware.AuthenticationMiddleware',
    'django.contrib.messages.middleware.MessageMiddleware',
    'django.middleware.clickjacking.XFrameOptionsMiddleware',
)

ROOT_URLCONF = 'rearvue.urls'

WSGI_APPLICATION = 'rearvue.wsgi.application'

DEFAULT_AUTO_FIELD = 'django.db.models.AutoField'

# Internationalization
# https://docs.djangoproject.com/en/1.6/topics/i18n/

LANGUAGE_CODE = 'en-us'

TIME_ZONE = 'UTC'

USE_I18N = True

USE_L10N = True

USE_TZ = True

FEEDS_USER_AGENT = "RearVue"
FEEDS_SERVER = settings_server.FEEDS_SERVER
FEEDS_CLOUDFLARE_WORKER = None

# Static files (CSS, JavaScript, Images)
# https://docs.djangoproject.com/en/1.6/howto/static-files/

STATIC_URL = '/static/'
MEDIA_URL = '/media/'


STATIC_ROOT = settings_server.STATIC_ROOT
MEDIA_ROOT = settings_server.MEDIA_ROOT

print(MEDIA_ROOT)





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
