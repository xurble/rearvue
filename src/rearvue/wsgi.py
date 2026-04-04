"""
WSGI config for the rearvue project.
Exposes the WSGI callable as a module-level variable named ``application``.
https://docs.djangoproject.com/en/stable/howto/deployment/wsgi/
"""

import os

from django.core.wsgi import get_wsgi_application

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'rearvue.settings')

application = get_wsgi_application()