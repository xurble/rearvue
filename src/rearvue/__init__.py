"""
Ensure TLS verification uses a current CA bundle when the environment does not
already configure one (see README: HTTPS / TLS).

If SSL_CERT_FILE or REQUESTS_CA_BUNDLE is set (e.g. in systemd, Docker, or
shell profile), that value is left unchanged.
"""

import os

if not os.environ.get("SSL_CERT_FILE") and not os.environ.get("REQUESTS_CA_BUNDLE"):
    try:
        import certifi
    except ImportError:
        pass
    else:
        _ca = certifi.where()
        os.environ["SSL_CERT_FILE"] = _ca
        os.environ["REQUESTS_CA_BUNDLE"] = _ca
