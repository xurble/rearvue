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

## Recent Updates

- **Instagram:** The `/rvadmin/` Instagram setup flow uses the Facebook Graph API (Business or Creator account). Background syncing is implemented in `src/rvservices/instagram_service.py` (see also `rvadmin` views for OAuth/token handling).

## "Supported" Services

|Service   |Imports archive|Live Updates|
|----------|:-------------:|:----------:|
|Twitter   | ✅            | ❌         |
|Flickr    | ✅            | ✅         |
|RSS       | ✅ (*)        | ✅         |
|Instagram | ✅            | ✅         |

**Note**: Instagram integration requires a Business or Creator account and uses the official Instagram Graph API.

Services that can be imported using RSS

- Github
- Mastodon


_*_ Can only import full archives if the source feed supports pagination.
