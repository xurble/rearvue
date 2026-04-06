# rearvue

A personal social media aggregator / nostalgia engine.

Collates your posts from around the web and brings them home.

Very, very rough and ready.

## HTTPS / TLS (certificate bundle)

On hosts where the Python/OpenSSL default store is missing or stale, outbound HTTPS (RSS, APIs, mirroring) can fail verification.

**Default behavior:** On import, the `rearvue` package sets `SSL_CERT_FILE` and `REQUESTS_CA_BUNDLE` to [certifi](https://pypi.org/project/certifi/)’s CA bundle **only if** neither variable is already set. Your environment or process manager can override both.

**Production:** Prefer the system trust store or your platform’s documented variables, for example:

```bash
export SSL_CERT_FILE=/path/to/ca-bundle.crt
# optional; requests/urllib3 also honor:
export REQUESTS_CA_BUNDLE="$SSL_CERT_FILE"
```

Do not disable TLS verification in application code; fix the CA configuration instead.

---

## Application configuration (`settings_server`)

Runtime settings live in `src/rearvue/settings_server.py`, which is **not** committed (see `.gitignore`). Create it next to `settings.py` and define at least:

| Variable | Purpose |
|----------|---------|
| `SECRET_KEY` | Django secret key |
| `DEBUG` | `True` / `False` |
| `ALLOWED_HOSTS` | List of hostnames the app answers on |
| `DATABASES` | Django database config; SQLite may use `NAME` with `__BASE_DIR__` (replaced at runtime) |
| `DATA_STORE` | Filesystem root where mirrored media and generated `rss.xml` files are written (must be writable) |
| `LOG_LOCATION` | Path to the rotating log file (non-debug / non-runserver) |
| `STATIC_ROOT`, `MEDIA_ROOT` | Static and media roots for collectstatic / uploads |
| `DEFAULT_DOMAIN_PROTOCOL` | `"http"` or `"https"` — used when building OAuth callback URLs if the site has no `alt_domain` |
| `FLICKR_KEY`, `FLICKR_SECRET` | Flickr API key and secret ([Flickr App Garden](https://www.flickr.com/services/apps/create/)) |
| `INSTAGRAM_KEY`, `INSTAGRAM_SECRET` | Meta app **Instagram App ID** and **Instagram App Secret** from the dashboard (Instagram Login / Business Login) |
| `FEEDS_SERVER` | Required by [django-feed-reader](https://pypi.org/project/django-feed-reader/) for RSS fetching; set per that package’s deployment docs |

Optional overrides (read in `rearvue/settings.py` via `getattr` where noted):

| Variable | Purpose |
|----------|---------|
| `INSTAGRAM_REDIRECT_URI` | Full OAuth redirect URL if it must differ from the auto-built `{protocol}://{host}/rvadmin/instagram_oauth_return/` |
| `INSTAGRAM_GRAPH_API_VERSION` | Default `v22.0` |
| `INSTAGRAM_OAUTH_SCOPES` | Default `instagram_business_basic` |
| `FACEBOOK_ACCESS_TOKEN` | Optional; not used by the current Instagram Login flow |

Install dependencies from `src/requirements.txt`, run migrations from `src/`, create a superuser, and collect static files as usual for Django.

---

## Sites and domains (`RVDomain`)

RearVue picks the site from the HTTP `Host` header (`rearvue.utils.page` / `admin_page`). For each hostname you serve:

1. In Django **admin**, open **RV domains** (app **rvsite**) and add a row.
2. Set **name** to the bare hostname visitors use (e.g. `example.com`), matching `ALLOWED_HOSTS`.
3. Set **alt_domain** (optional) to the full public origin if it differs, e.g. `https://www.example.com`. Flickr and Instagram OAuth callbacks prefer this when set; if the value has no scheme, `DEFAULT_DOMAIN_PROTOCOL` is prepended.
4. Set **owner** to a Django user (typically your superuser).

Wrong or missing **name** / **alt_domain** leads to 404 in `/rvadmin/` or OAuth redirect mismatches.

---

## Admin UIs

- **Public site:** `/` (per `rvsite` URLs).
- **Django admin:** `/admin/` — use this to create and edit `RVDomain`, `RVService`, and content models.
- **Site admin (RearVue):** `/rvadmin/` — superuser only; lists services for the current domain and runs connect flows.

---

## Configuring each service

`RVService` rows tie a source to a domain. **type** must match exactly what the workers expect (lowercase): `rss`, `twitter`, `flickr`, `instagram`.

### RSS (`type = rss`)

Used for any feed [django-feed-reader](https://pypi.org/project/django-feed-reader/) can poll (blogs, GitHub releases, Mastodon RSS, etc.).

1. In Django **admin**, add **RV services** → choose **domain**, set **type** to `rss`, set **name** as you like.
2. Set **auth token** to the **full feed URL** (the code stores the feed URL in this field).
3. Ensure **live** is checked when you want `update_content` to crawl it.
4. Run `python manage.py update_content` (or your cron equivalent) so `update_rss` runs; feeds are mirrored when enclosures exist.

Pagination depth depends on the feed itself.

### Twitter (`type = twitter`)

Live Twitter API updates are not implemented; import is via **X/Twitter archive**.

1. In Django **admin**, add **RV services** with **type** `twitter`, correct **domain**, and **username** set to your handle (no `@`) — used when building status URLs during import.
2. Visit `/rvadmin/twitter_connect/<service_id>/` (the id is the primary key of that `RVService`).
3. Upload the archive **`tweets.js`** file using the “Import Twitter Archive” form (the file should start like `window.YTD.tweets.part0 = ` as produced by the archive).

The “Connect” action in the template is a stub (“Broken :(”).

After import, `update_content` runs mirroring and link discovery for Twitter items when you skip nothing.

### Flickr (`type = flickr`)

1. Configure **FLICKR_KEY** and **FLICKR_SECRET** in `settings_server`.
2. Open `/rvadmin/flickr_connect/new/` (POST “Connect”) to create a service and start OAuth, or open `/rvadmin/flickr_connect/<id>/` for an existing Flickr service.
3. Flickr redirects back to `/rvadmin/flickr_return/?svc=<id>`; callback URL is built from **alt_domain** or `DEFAULT_DOMAIN_PROTOCOL` + **RVDomain.name**. Register the same callback URL in the Flickr app settings if required.
4. After authorization, tokens are stored on the `RVService` row.

### Instagram (`type = instagram`)

Requires a **Business or Creator** Instagram account and a [Meta developer app](https://developers.facebook.com/apps/) with **Instagram** product and **Instagram API with Instagram Login** (Business Login) configured.

1. In the Meta app, add **Valid OAuth Redirect URIs** that match what RearVue will send. Typically that is exactly:
   - `https://<your-host>/rvadmin/instagram_oauth_return/`  
   or, if you use **INSTAGRAM_REDIRECT_URI**, that exact string (no trailing slash mismatch).
2. Set **INSTAGRAM_KEY** and **INSTAGRAM_SECRET** in `settings_server` to the app’s Instagram App ID and Instagram App Secret from the Business Login section of the dashboard.
3. Open `/rvadmin/instagram_connect/new/`, submit **Connect**, complete Instagram login, and return to the site while still logged into Django (same browser session).
4. Tokens and IG user id are stored on the service; run `update_content` to sync. The API returns up to on the order of **10,000** recent media per account; stories are not the same as feed media in the API.

To wipe all Instagram services, items, and mirrored files and start over:

```bash
cd src && python manage.py reset_instagram_graph --confirm
```

---

## Refreshing content (cron)

From the `src/` directory:

```bash
python manage.py update_content
```

Flags: `--skip-rss`, `--skip-twitter`, `--skip-instagram`, `--skip-flickr`, `--skip-cleanup` (skips domain year bounds and per-domain `media/<domain>/rss.xml` generation).

Typical production use: run without skips on a schedule; ensure `DATA_STORE` (and DB) are persistent.

---

## Supported services (summary)

| Service   | Imports archive | Live updates |
|-----------|:---------------:|:------------:|
| Twitter   | ✅              | ❌           |
| Flickr    | ✅              | ✅           |
| RSS       | ✅ (*)          | ✅           |
| Instagram | ✅              | ✅           |

\* Full history only if the feed exposes it (pagination).

Other sources can be aggregated indirectly **via RSS** (e.g. GitHub, Mastodon) if they publish a feed URL.
