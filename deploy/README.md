# Production ASGI deployment

RearVue's Django site and optional MCP endpoint share `rearvue.asgi:application`.
The checked-in examples implement this production request path:

```text
Cloudflare TLS -> Nginx TLS -> Gunicorn -> Uvicorn worker -> Django ASGI
```

The ordinary Django views remain compatible because Django adapts synchronous
views inside the ASGI application. MCP is mounted at `/mcp` and `/mcp/` only.

## Repository-provided configuration

- `gunicorn.conf.py` runs the supported external `uvicorn-worker` worker class,
  listens on loopback, trusts forwarded headers only from the local proxy, and
  keeps its default timeout below Cloudflare's edge timeout.
- `nginx/rearvue.conf.example` preserves the MCP path and bearer header, disables
  response/request buffering and caching for MCP, sets bounded timeouts and body
  size, and forwards the rest of the Django site to the same ASGI process.
- `systemd/rearvue.service.example` starts and supervises the Gunicorn process.
- `rearvue.env.example` lists the environment consumed by both Django and
  Gunicorn; install the filled copy with service-account-only permissions.
- `cloudflare/README.md` lists the matching account-side controls.
- `../src/rearvue/settings_server.py.example` supplies production Django and MCP
  settings from environment variables without committing secrets.

Install dependencies from the repository root:

```shell
python -m venv .venv
.venv/bin/pip install -r src/requirements.txt
```

Before running any Django command, copy `src/rearvue/settings_server.py.example`
to the ignored `src/rearvue/settings_server.py`. Install a filled,
service-account-readable copy of `deploy/rearvue.env.example` at
`/etc/rearvue/rearvue.env`, create the configured data/log/static/media
directories, and make the writable paths accessible to the service account.
Load that same environment into the administrative shell, then prepare Django:

```shell
set -a
. /etc/rearvue/rearvue.env
set +a
cd src
../.venv/bin/python manage.py migrate
../.venv/bin/python manage.py collectstatic --noinput
```

Keep the installed environment file shell-compatible and readable only by the
administrator and service group. Adjust the example hostname, certificate
paths, filesystem paths, service user/group, and static/media aliases before
installing the Nginx and systemd files. Keep Django's `CONN_MAX_AGE` at zero for
ASGI; use a database-backend-supported pool if persistent connection reuse is
required.

From `src/`, the equivalent foreground command is:

```shell
../.venv/bin/gunicorn --config ../deploy/gunicorn.conf.py rearvue.asgi:application
```

Stop web/import writers while applying migrations, then enable MCP only after
migrations and client provisioning are complete. Apply the Cloudflare controls
last, then test both the ordinary site and an MCP initialize request through the
public hostname.
