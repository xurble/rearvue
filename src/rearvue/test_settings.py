"""Secret-free settings for the repository's automated test suite."""

# ruff: noqa: I001

import sys
from pathlib import Path
from types import ModuleType


TEST_DATA_ROOT = Path("/tmp/rearvue-test-data")

settings_server = ModuleType("rearvue.settings_server")
settings_server.SECRET_KEY = "rearvue-test-key"
settings_server.DEBUG = True
settings_server.LOG_LOCATION = "/tmp/rearvue-test.log"
settings_server.DATA_STORE = str(TEST_DATA_ROOT)
settings_server.DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.sqlite3",
        "NAME": ":memory:",
    }
}
settings_server.FLICKR_KEY = "test-flickr-key"
settings_server.FLICKR_SECRET = "test-flickr-secret"
settings_server.INSTAGRAM_KEY = "test-instagram-key"
settings_server.INSTAGRAM_SECRET = "test-instagram-secret"
settings_server.DEFAULT_DOMAIN_PROTOCOL = "https"
settings_server.ALLOWED_HOSTS = ["testserver", "example.com"]
settings_server.FEEDS_SERVER = ""
settings_server.STATIC_ROOT = "/tmp/rearvue-test-static"
settings_server.MEDIA_ROOT = "/tmp/rearvue-test-media"
settings_server.MCP_ENABLED = True
settings_server.MCP_ALLOWED_HOSTS = ["testserver"]
settings_server.MCP_ALLOWED_ORIGINS = []
sys.modules["rearvue.settings_server"] = settings_server

from .settings import *


LOGGING = {"version": 1, "disable_existing_loggers": False}
PASSWORD_HASHERS = ["django.contrib.auth.hashers.MD5PasswordHasher"]
