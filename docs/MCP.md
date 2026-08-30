# RearVue MCP server (contract v1)

RearVue exposes an optional authenticated Model Context Protocol server for normalized archive data. It uses stateless Streamable HTTP at `/mcp`, is disabled by default, and requires Django's ASGI entry point.

This first contract slice supports discovery, granted domain and safe service reads, item retrieval/search, normalized item create/upsert/update, bounded bulk upsert, optimistic concurrency, idempotency, and mutation auditing. Media/link mutation, exports, jobs, archive submission, service mutation, processing, and deletion are deferred to [issue 90](https://github.com/xurble/rearvue/issues/90).

## Setup and deployment

Copy `rearvue/settings_server.py.example` to the ignored
`rearvue/settings_server.py`, then add deployment-specific values:

```python
MCP_ENABLED = True
MCP_ALLOWED_HOSTS = ["archive.example.com"]
MCP_ALLOWED_ORIGINS = []
```

`MCP_ALLOWED_HOSTS` defaults to Django's `ALLOWED_HOSTS`, but an explicit exact list is recommended. A host followed by `:*` permits any port for that host. Browser clients must send an Origin listed exactly in `MCP_ALLOWED_ORIGINS`; non-browser clients normally omit Origin.

Optional limit settings and defaults:

```python
MCP_DEFAULT_PAGE_SIZE = 50
MCP_MAX_PAGE_SIZE = 100
MCP_MAX_BULK_ITEMS = 100
MCP_MAX_RAW_DATA_BYTES = 256 * 1024
MCP_MAX_REQUEST_BODY_BYTES = 2 * 1024 * 1024
MCP_IDEMPOTENCY_TTL_SECONDS = 24 * 60 * 60
```

Stop Gunicorn and importer/background writers before applying migrations so no
new duplicate external identity can appear between the fail-safe preflight and
the database constraint. Then create a client in Django admin under
**RearVue MCP → MCP clients**. Choose scopes and domains. Leaving the token field
blank generates a high-entropy token; copy the warning after saving because
RearVue stores only its SHA-256 digest. Operators may instead supply a token
matching `rvmcp_<8 lowercase hex characters>_<at least 32 URL-safe characters>`.

For production, serve `rearvue.asgi:application` through Gunicorn with the
external Uvicorn worker package included in RearVue's requirements:

```shell
cd src
../.venv/bin/gunicorn --config ../deploy/gunicorn.conf.py rearvue.asgi:application
```

The repository includes matching [production deployment examples](../deploy/README.md)
for Cloudflare TLS, Nginx, systemd, Gunicorn, and Django settings. The Nginx MCP
location preserves `Authorization`, disables proxy buffering and caching, and
uses a two-MiB body limit matching the Django default. MCP responses also emit
`Cache-Control: no-store` and `X-Accel-Buffering: no` from the application.

Cloudflare must use Full (strict) mode so the Nginx hop is HTTPS. Its account-side
cache bypass, WAF, and origin-access controls are documented separately because
they cannot be committed as active infrastructure from this repository. Default
Gunicorn and Nginx request timeouts are 120 seconds, below Cloudflare's current
125-second proxied read timeout; long-running work belongs in an asynchronous job
slice rather than an open request.

Direct Uvicorn remains suitable for local development:

```shell
cd src
uvicorn rearvue.asgi:application --host 127.0.0.1 --port 8000
```

The same ASGI process serves the ordinary synchronous Django site; Django adapts
those views. The existing WSGI entry point may still serve a separate ordinary
site process, but WSGI does not expose MCP. Configure clients for
`https://archive.example.com/mcp/` with `Authorization: Bearer rvmcp_…`.

The endpoint returns 404 while disabled, 401 for missing/invalid/expired/disabled credentials, 403 for an unapproved browser Origin, and 421 for an unapproved Host.

## Authorization

Every request is authenticated. Domain grants and scopes are independent and both are enforced:

- `domains:read`: list and retrieve granted domain metadata.
- `services:read`: list and retrieve services in granted domains.
- `items:read`: retrieve and search items in granted domains.
- `items:raw`: include parsed `raw_data` in item results; it has no effect without item access.
- `items:write`: create, upsert, and update items through granted services.

An inaccessible or nonexistent domain, service, or item returns the same `not_found` error to prevent identifier probing. Service responses never include legacy `config`, `credentials`, or `state` documents.

## Discovery and tools

Tool names carry the contract major version:

- `rearvue_v1_discover`
- `rearvue_v1_list_domains`
- `rearvue_v1_get_domain`
- `rearvue_v1_list_services`
- `rearvue_v1_get_service`
- `rearvue_v1_search_items`
- `rearvue_v1_get_item`
- `rearvue_v1_create_item`
- `rearvue_v1_upsert_item`
- `rearvue_v1_update_item`
- `rearvue_v1_bulk_upsert_items`

`rearvue_v1_discover` reports versions, the caller's grants, limits, identity/update semantics, caption policy, current media/link support, importers, and export formats. MCP's `tools/list` publishes exact JSON schemas from the tool signatures.

Expected application failures are structured and do not leak tracebacks:

```json
{
  "ok": false,
  "error": {
    "code": "validation_error",
    "message": "Field is required.",
    "path": "datetime_created",
    "retryable": false
  }
}
```

Stable codes include `validation_error`, `limit_exceeded`, `forbidden`, `not_found`, `identity_conflict`, `expected_revision_required`, `revision_conflict`, `idempotency_conflict`, `invalid_cursor`, and `internal_error`.

## Normalized item writes

The stable external identity is `(service_id, item_id)`, enforced by a database constraint. The migration refuses to add the constraint if existing duplicates are present and reports sample identities; it never deletes or merges archive data.

Create example:

```json
{
  "item": {
    "service_id": 7,
    "item_id": "future-network-123",
    "datetime_created": "2026-08-30T10:15:00+00:00",
    "remote_url": "https://social.example/posts/123",
    "title": "Imported by an agent",
    "caption": "Plain text is the default <and is escaped>.",
    "public": true,
    "moderated": true,
    "edited": false,
    "raw_data": {"source_specific_field": "preserved"}
  }
}
```

Client-writable fields are `datetime_created`, `remote_url`, `title`, `caption`, `caption_format`, `public`, `moderated`, `edited`, and `raw_data`. `service_id` and `item_id` are supplied during creation and immutable afterward. RearVue derives `domain`, `date_created`, `date_retrieved`, and `slug`; `mirror_state` is server-managed.

`raw_data` must be JSON and is stored canonically. Its encoded UTF-8 size is capped by `MCP_MAX_RAW_DATA_BYTES`. It is omitted from responses unless the caller has `items:raw`. Invalid legacy payloads are returned as `null` with a warning rather than exposed as arbitrary text.

### Caption safety

`caption_format` defaults to `plain`. Plain text is HTML-escaped and newlines become `<br>` because legacy item-detail rendering treats stored captions as HTML.

Explicit `caption_format: "html"` is sanitized before storage. The allowlist is `a`, `br`, `blockquote`, `code`, `em`, `li`, `ol`, `p`, `pre`, `strong`, and `ul`; links may carry only `href` and `title`, using `http`, `https`, or `mailto`. Scripts, event handlers, unsafe protocols, and other markup are stripped.

### Upsert and conflict behavior

Creates fail with `identity_conflict` if the identity exists. Upsert creates a missing identity. For an existing identity:

- an identical supplied representation is a successful no-op;
- a change requires `expected_revision`;
- the revision is compared atomically and a mismatch returns `revision_conflict` plus `current_revision`.

Updates are patches: omitted writable fields remain unchanged. `expected_revision` is mandatory. Revisions increment for ordinary model saves as well as MCP updates, so admin/importer changes participate in conflict detection. Deliberate direct `QuerySet.update()` operations bypass model hooks and must maintain revision explicitly.

### Bulk upsert and idempotency

`rearvue_v1_bulk_upsert_items` requires `items` and `idempotency_key` and accepts at most `MCP_MAX_BULK_ITEMS` records. Records run independently; the result contains the original zero-based index and success or structured failure for every submission.

Complete results are retained for 24 hours by default. Repeating the same operation, key, and input returns the stored result. Reusing the key with different input returns `idempotency_conflict`. A mixed result has `ok: true` for the processed batch plus counts and per-record outcomes.

## Search and pagination

Item ordering is always `(datetime_created DESC, id DESC)`. The opaque next cursor is bound to filters and expansion flags; changing either produces `invalid_cursor`. Default page size is 50 and maximum is 100.

Supported filters:

- `domain_ids`, `service_ids`, `service_types`, and exact source `item_id`
- `created_from`, `created_to`, `retrieved_from`, and `retrieved_to` (timezone-aware ISO 8601)
- case-insensitive substring `text` across title/caption and `remote_url`
- `public`, `moderated`, `edited`, and `mirror_state`
- `has_media`, `media_types`, `has_links`, and `link_is_context`

Example:

```json
{
  "filters": {
    "domain_ids": [3],
    "service_types": ["twitter"],
    "created_from": "2025-01-01T00:00:00+00:00",
    "text": "launch",
    "public": true,
    "has_media": true
  },
  "limit": 50,
  "include_media": true,
  "include_links": true
}
```

Media expansion contains only record ID, numeric type, general medium, and inferred MIME type; it never returns filesystem paths. Link expansion returns authorized archive link metadata. Both are read-only in contract v1.

## Auditing

Create, upsert, update, and bulk operations write audit records containing client, domain where singular, operation, affected IDs/count, timestamp, outcome, idempotency key where applicable, and non-sensitive counts/codes. Audit records exclude bearer tokens, raw payloads, captions, credentials, and settings. They are read-only in admin and are not exposed as an MCP tool in this slice.
