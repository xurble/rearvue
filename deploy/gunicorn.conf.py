"""Gunicorn configuration for RearVue's combined Django/MCP ASGI application."""

import os


bind = os.getenv("REARVUE_BIND", "127.0.0.1:8000")
worker_class = "uvicorn_worker.UvicornWorker"
# Keep the default conservative for a database-backed ASGI app. Tune this with
# production load tests and the database connection budget.
workers = int(os.getenv("REARVUE_WORKERS", "3"))

# Cloudflare's default proxied read timeout is 125 seconds. Keep application
# work below that ceiling and stop a stuck worker just before the edge does.
timeout = int(os.getenv("REARVUE_WORKER_TIMEOUT", "120"))
graceful_timeout = int(os.getenv("REARVUE_GRACEFUL_TIMEOUT", "30"))
keepalive = int(os.getenv("REARVUE_KEEPALIVE", "5"))
max_requests = int(os.getenv("REARVUE_MAX_REQUESTS", "1000"))
max_requests_jitter = int(os.getenv("REARVUE_MAX_REQUESTS_JITTER", "100"))

# Only trust proxy headers from the local Nginx hop by default. Override with a
# comma-separated network list when Nginx connects from another trusted address.
forwarded_allow_ips = os.getenv("REARVUE_FORWARDED_ALLOW_IPS", "127.0.0.1,::1")

accesslog = "-"
errorlog = "-"
capture_output = True
