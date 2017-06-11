"""
Django settings for rearvue project.

For more information on this file, see
https://docs.djangoproject.com/en/1.6/topics/settings/

For the full list of settings and their values, see
https://docs.djangoproject.com/en/1.6/ref/settings/
"""

# Build paths inside the project like this: os.path.join(BASE_DIR, ...)
import os

import settings_server

import os
BASE_DIR = os.path.dirname(os.path.dirname(__file__))
print BASE_DIR

# Quick-start development settings - unsuitable for production
# See https://docs.djangoproject.com/en/1.6/howto/deployment/checklist/

# SECURITY WARNING: keep the secret key used in production secret!
SECRET_KEY = settings_server.SECRET_KEY

# SECURITY WARNING: don't run with debug turned on in production!
DEBUG = settings_server.DEBUG


FLICKR_KEY =    settings_server.FLICKR_KEY
FLICKR_SECRET = settings_server.FLICKR_SECRET

INSTAGRAM_CLENT_ID      = settings_server.INSTAGRAM_CLENT_ID
INSTAGRAM_CLIENT_SECRET = settings_server.INSTAGRAM_CLIENT_SECRET


DEFAULT_DOMAIN = settings_server.DEFAULT_DOMAIN
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
)

MIDDLEWARE_CLASSES = (
    'django.contrib.sessions.middleware.SessionMiddleware',
    'django.middleware.common.CommonMiddleware',
    'django.middleware.csrf.CsrfViewMiddleware',
    'django.contrib.auth.middleware.AuthenticationMiddleware',
    'django.contrib.messages.middleware.MessageMiddleware',
    'django.middleware.clickjacking.XFrameOptionsMiddleware',
)

ROOT_URLCONF = 'rearvue.urls'

WSGI_APPLICATION = 'rearvue.wsgi.application'


DATABASES = settings_server.DATABASES

# Internationalization
# https://docs.djangoproject.com/en/1.6/topics/i18n/

LANGUAGE_CODE = 'en-us'

TIME_ZONE = 'UTC'

USE_I18N = True

USE_L10N = True

USE_TZ = False


# Static files (CSS, JavaScript, Images)
# https://docs.djangoproject.com/en/1.6/howto/static-files/

STATIC_URL = '/static/'

STATICFILES_DIRS = (
    os.path.join(BASE_DIR, "static"),
    os.path.join(BASE_DIR, "..", "data_store"),
)

print STATICFILES_DIRS

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
