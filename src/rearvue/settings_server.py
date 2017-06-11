

import os
BASE_DIR = os.path.dirname(os.path.dirname(__file__))


if "/Users/g/" in BASE_DIR:
    print "local"
    DATABASES = {
        'default': {
            'ENGINE': 'django.db.backends.mysql',
            'NAME': 'rearvue',
            'USER': 'root',
            'PASSWORD': '',
            'HOST':'localhost',
            'PORT':'3306',
            'OPTIONS': {'charset': 'utf8mb4'}
        }
    }
    DEFAULT_DOMAIN = "rearvue.local:8000"
    ALLOWED_HOSTS = []
    DEBUG = True
    DEFAULT_DOMAIN_PROTOCAL = "http"
    
else:
    DATABASES = {
        'default': {
            'ENGINE': 'django.db.backends.mysql',
            'NAME': 'cruisem1_rearvue',
            'USER': 'cruisem1_rv',
            'PASSWORD': '9#ZM+SV@2A}J',
            'HOST':'localhost',
            'PORT':'3306',
            'OPTIONS': {'charset': 'utf8mb4'}
        }
    }
    ALLOWED_HOSTS = [".xurble.org"]
    DEBUG = False
    DEFAULT_DOMAIN = "xurble.org"
    DEFAULT_DOMAIN_PROTOCAL = "http"

SECRET_KEY = '(%u%*-z$@!3o!4tt#%2ha5bo_at(=*kb3a@w-(j_ki0w41-ab%'

FLICKR_KEY =    "46d5bd14dd741b824e3de01335c1aed7"
FLICKR_SECRET = "c8cbafb06c827402"

INSTAGRAM_CLENT_ID      = "01931b9af95c4b36a96bf2750f140ab7"
INSTAGRAM_CLIENT_SECRET = "c99bcd7e4df24a8fa19ecaf28ca0f8fd"


 