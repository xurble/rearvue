# RearVue MCP server (contract v1.1)

RearVue exposes an optional authenticated Model Context Protocol server for normalized archive data. It uses stateless Streamable HTTP at `/mcp`, is disabled by default, and requires Django's ASGI entry point.

Contract v1.1 keeps the `rearvue_v1_*` tool names while advancing the additive contract. It supports domain-scoped item, link, media, and service management; incremental and asynchronous export; durable named jobs; shared Twitter archive import; authenticated downloads; and preview-confirmed bounded deletion. It never exposes provider credentials, Django authentication data, signing secrets, arbitrary commands, or arbitrary filesystem paths.

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
MCP_GENERATED_ROOT = "/srv/rearvue/data/mcp-generated"
MCP_MAX_JOB_ATTEMPTS = 3
MCP_JOB_LEASE_SECONDS = 60
MCP_JOB_RETRY_BASE_SECONDS = 5
MCP_JOB_RESULT_MAX_BYTES = 256 * 1024
MCP_DESTRUCTIVE_PREVIEW_TTL_SECONDS = 5 * 60
MCP_MAX_DESTRUCTIVE_RECORDS = 100
MCP_MAX_MEDIA_BYTES = 25 * 1024 * 1024
MCP_MAX_IMAGE_PIXELS = 40_000_000
MCP_MAX_LINK_RESPONSE_BYTES = 1024 * 1024
MCP_MAX_ARCHIVE_BYTES = 2 * 1024 * 1024
MCP_MAX_ARCHIVE_RECORDS = 10_000
MCP_ARTIFACT_TTL_SECONDS = 24 * 60 * 60
```

Run migrations, then create a client in Django admin under **RearVue MCP → MCP clients**. Choose scopes and domains. Leaving the token field blank generates a high-entropy token; copy the warning after saving because RearVue stores only its SHA-256 digest. Operators may instead supply a token matching `rvmcp_<8 lowercase hex characters>_<at least 32 URL-safe characters>`.

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

Every request is authenticated. The single `domain:owner` capability and explicit domain grants are independent and both are enforced. Migration to v1.1 replaces every existing client's legacy granular scopes with `domain:owner` while preserving its domain grants.

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
- `rearvue_v1_list_links`, `rearvue_v1_get_link`, `rearvue_v1_create_link`, `rearvue_v1_update_link`
- `rearvue_v1_list_media`, `rearvue_v1_get_media`, `rearvue_v1_create_media`, `rearvue_v1_update_media`
- `rearvue_v1_create_service`, `rearvue_v1_update_service`
- `rearvue_v1_list_jobs`, `rearvue_v1_get_job`, `rearvue_v1_submit_processing`
- `rearvue_v1_export_json`, `rearvue_v1_submit_export`
- `rearvue_v1_submit_twitter_archive`
- `rearvue_v1_preview_delete`, `rearvue_v1_confirm_delete`

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

Stable codes include `validation_error`, `limit_exceeded`, `forbidden`, `not_found`, `unsafe_url`, `unsafe_path`, `unsupported_media`, `identity_conflict`, `expected_revision_required`, `revision_conflict`, `idempotency_conflict`, `invalid_cursor`, `invalid_confirmation`, `confirmation_used`, `confirmation_expired`, `impact_changed`, and `internal_error`.

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

`raw_data` must be JSON and is stored canonically. Its encoded UTF-8 size is capped by `MCP_MAX_RAW_DATA_BYTES`. It is available to callers with `domain:owner`. Invalid legacy payloads are returned as `null` with a warning rather than exposed as arbitrary text.

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

Media expansion contains only record ID, numeric type, general medium, inferred MIME type, revision, update timestamp, and an authenticated download reference; it never returns filesystem paths. Link expansion returns authorized archive link metadata plus revision information.

## Links and media

Link and media list/get/create/update tools enforce both `domain:owner` and the caller's explicit domain grants. Updates require `expected_revision`. Link URLs must be public HTTP(S) destinations; private, loopback, link-local, reserved, multicast, `.local`, and cloud-metadata destinations are rejected before enrichment, and every redirect target is revalidated.

Media creation and replacement accept exactly one of `content_base64` or `source_url`. Remote downloads are streamed and stopped at `MCP_MAX_MEDIA_BYTES`. JPEG, PNG, WebP, GIF, and MP4 are recognized by content signature rather than filename or declared content type. Images are decoded and verified with Pillow and bounded by `MCP_MAX_IMAGE_PIXELS`; corrupt or mismatched content is rejected. Writes use a private temporary file, flush and `fsync`, then atomically replace a random generated filename.

Generated media must live under `MCP_GENERATED_ROOT`, and that root must itself be contained by `DATA_STORE`. Resolution rejects symlink roots, symlink files, non-regular files, traversal, and files outside controlled storage. Replacement deletes the previous controlled file only after the database transaction commits.

Example media creation arguments:

```json
{"media": {"item_id": 42, "source_url": "https://cdn.example/photo.webp"}}
```

Media downloads use `/mcp-download/media/<id>/` and require the same bearer token and domain grant as MCP. Responses are attachments with `Cache-Control: no-store` and `X-Content-Type-Options: nosniff`. No client-supplied path is ever resolved.

## Exports

`rearvue_v1_export_json` returns deterministic records ordered by record family and stable integer ID. Filters support `domain_ids`, `kinds`, and a timezone-aware `updated_after`. The first page fixes a snapshot timestamp; its opaque cursor binds the snapshot and filters, so later writes do not leak into a resumed export. Families are domains, services, items, media manifests, and links. Media manifests contain authenticated download references, never stored paths. Service configuration, credentials, state, bearer tokens, Django users, settings, and signing material are excluded.

`rearvue_v1_submit_export` creates a durable single-domain NDJSON job. The worker writes atomically under `MCP_GENERATED_ROOT/exports`, computes a SHA-256 checksum and byte size, records resumability metadata and expiry, and exposes `/mcp-download/jobs/<id>/` only to the client that submitted the job. ZIP packaging is not supported.

Example incremental export:

```json
{"filters": {"domain_ids": [3], "kinds": ["items", "media_manifest"], "updated_after": "2026-01-01T00:00:00+00:00"}, "limit": 100}
```

## Jobs and named processing

`rearvue_v1_list_jobs` and `rearvue_v1_get_job` expose progress, attempts, warnings, sanitized failures, terminal results, and safe artifact metadata; submitted payloads are deliberately omitted. `rearvue_v1_submit_processing` accepts only these named operations:

- `domain_metadata_refresh`, with no selector;
- `media_mirror`, with a bounded `item_ids` selector inside one granted domain;
- `link_enrichment`, with a bounded `link_ids` selector inside one granted domain.

The registry does not accept module names, callables, management commands, SQL, Python, shell, or paths. Selector IDs are rechecked against the job domain before execution. Link enrichment downloads only bounded public HTML and stores bounded title, description, and a separately validated public Open Graph image URL.

## Twitter archive import

`rearvue_v1_submit_twitter_archive` accepts a bounded base64-encoded Twitter `tweets.js` file for a Twitter service in a granted domain. The same `import_twitter_archive` application service is called by the MCP job and the existing admin upload. It validates the JavaScript wrapper, UTF-8, JSON shape, record count, tweet identity and timestamp; uses `(service, tweet id)` idempotency; HTML-escapes generated captions; stores canonical bounded JSON; reports per-record failures without content; and never attempts social login or archive download.

Example submission:

```json
{"domain_id": 3, "service_id": 7, "archive_base64": "d2luZG93LllURC50d2VldHMuLi4="}
```

## Service management

Service creation accepts only `domain_id`, `name`, supported `type`, `live`, and `hide_unmoderated`. Updates require `expected_revision` and accept only `name`, `live`, and `hide_unmoderated`; type is immutable. `config`, `credentials`, and `state` are neither accepted nor returned. Service deletion is not exposed.

## Preview-confirmed deletion and recovery

Deletion is limited to explicit item, media, or link IDs in one granted domain, with at most `MCP_MAX_DESTRUCTIVE_RECORDS` top-level records. `rearvue_v1_preview_delete` returns exact IDs/revisions, related item/media/link counts, poster impact, controlled-file count, a cryptographically random confirmation token, and an expiry. Only the SHA-256 token digest is stored.

`rearvue_v1_confirm_delete` requires the same client, domain grant, unused token, and unexpired preview. It locks the preview and recomputes the complete impact; any revision, relation, count, or poster change returns `impact_changed` and requires a new preview. A successful token is single-use. Database deletion is irreversible. Deleting a poster item clears the domain's poster reference instead of cascading to the domain.

Controlled generated files are deleted only after the database transaction commits. Cleanup never follows symlinks or removes files outside `MCP_GENERATED_ROOT`. Missing files are already clean; unsafe or failed cleanup is retained in the durable mutation audit as a sanitized failure code. Operators recover database content from their normal database backups and generated files from storage backups; RearVue provides no soft-delete or automatic recovery window.

Example flow:

```json
{"domain_id": 3, "resource": "media", "ids": [81, 82]}
```

Pass the returned `preview.id` and `confirmation_token` unchanged to `rearvue_v1_confirm_delete` within five minutes.

## Durable worker foundation

Run at least one separate database-backed worker process in production:

```shell
cd src
python manage.py run_mcp_jobs
```

For supervised or batch execution, `--max-jobs N` exits after processing up to `N` jobs. `--once` performs one claim attempt, and `--worker-id` supplies a stable operator-visible identity. Polling is bounded by `--poll-interval`, which accepts 0.05–60 seconds.

Jobs store their operation name, domain/client ownership, bounded JSON payload and result, progress, warnings/failures, attempts, lease, heartbeat, terminal timestamps, and controlled artifact metadata. Claims use a guarded database update, so a queued job can be won only once. Workers extend leases through heartbeats. A later worker recovers expired leases, retries with bounded exponential backoff, and records a terminal `lease_expired` result after the attempt limit.

The worker dispatches only names present in the in-process operation registry; stored payloads can never select a Python callable, management command, shell command, SQL statement, or filesystem path. Registered operations cover domain metadata refresh, bounded media mirroring, bounded link enrichment, NDJSON export, and Twitter archive import.

## Auditing

Create, upsert, update, bulk, job submission, export, preview, and confirmed deletion operations write audit records containing client, domain where singular, operation, affected IDs/count, timestamp, outcome, idempotency key where applicable, and non-sensitive counts/codes. Confirmed deletion also retains its exact impact and post-commit cleanup failure codes. Audit records exclude bearer tokens, confirmation tokens, raw payloads, captions, credentials, archive content, paths, and settings. They are read-only in admin and are not exposed as an MCP tool.
