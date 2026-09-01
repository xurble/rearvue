import base64
import binascii
import hashlib
import json
import os
import secrets
import tempfile
from datetime import timedelta
from pathlib import Path

from django.conf import settings
from django.utils import timezone
from django.utils.dateparse import parse_datetime

from rvsite.models import RVDomain, RVItem, RVLink, RVMedia, RVService

from .capabilities import _generated_root, serialize_link, serialize_media
from .jobs import enqueue_job
from .models import MCPAuditRecord
from .services import (
    MCPServiceError,
    accessible_domain_ids,
    audit,
    canonical_hash,
    require_domain,
    require_scope,
    serialize_domain,
    serialize_item,
    serialize_service,
)

EXPORT_KINDS = ("domains", "services", "items", "media_manifest", "links")


def _parse_export_time(value, path):
    if value is None:
        return None
    if not isinstance(value, str):
        raise MCPServiceError("validation_error", "Expected an ISO 8601 datetime.", path=path)
    parsed = parse_datetime(value)
    if parsed is None or timezone.is_naive(parsed):
        raise MCPServiceError("validation_error", "Datetime must include a timezone.", path=path)
    return parsed


def normalize_export_filters(client, filters=None):
    filters = filters or {}
    if not isinstance(filters, dict):
        raise MCPServiceError("validation_error", "filters must be an object.", path="filters")
    unknown = sorted(set(filters) - {"domain_ids", "kinds", "updated_after"})
    if unknown:
        raise MCPServiceError("validation_error", f"Unknown filters: {', '.join(unknown)}.", path=f"filters.{unknown[0]}")
    granted = set(accessible_domain_ids(client))
    requested = filters.get("domain_ids", sorted(granted))
    if not isinstance(requested, list) or not requested:
        raise MCPServiceError("validation_error", "domain_ids must be a non-empty list.", path="filters.domain_ids")
    try:
        domain_ids = sorted({int(value) for value in requested})
    except (TypeError, ValueError) as exc:
        raise MCPServiceError("validation_error", "domain_ids must contain integers.", path="filters.domain_ids") from exc
    if not set(domain_ids).issubset(granted):
        raise MCPServiceError("not_found", "One or more domains were not found.", path="filters.domain_ids")
    kinds = filters.get("kinds", list(EXPORT_KINDS))
    if not isinstance(kinds, list) or not kinds or not set(kinds).issubset(EXPORT_KINDS):
        raise MCPServiceError(
            "validation_error", f"kinds must contain values from {', '.join(EXPORT_KINDS)}.", path="filters.kinds"
        )
    ordered_kinds = [kind for kind in EXPORT_KINDS if kind in set(kinds)]
    updated_after = _parse_export_time(filters.get("updated_after"), "filters.updated_after")
    return {
        "domain_ids": domain_ids,
        "kinds": ordered_kinds,
        "updated_after": updated_after.isoformat() if updated_after else None,
    }


def _encode_export_cursor(snapshot, kind, last_id, binding):
    payload = {
        "v": 1,
        "snapshot": snapshot.isoformat(),
        "kind": kind,
        "last_id": last_id,
        "binding": canonical_hash(binding),
    }
    return base64.urlsafe_b64encode(json.dumps(payload, separators=(",", ":")).encode()).decode().rstrip("=")


def _decode_export_cursor(cursor, binding):
    if not cursor:
        return timezone.now(), None, 0
    try:
        padded = cursor + "=" * (-len(cursor) % 4)
        payload = json.loads(base64.urlsafe_b64decode(padded).decode())
        snapshot = _parse_export_time(payload["snapshot"], "cursor")
        kind = payload["kind"]
        last_id = int(payload["last_id"])
        if (
            payload.get("v") != 1
            or payload.get("binding") != canonical_hash(binding)
            or kind not in EXPORT_KINDS
            or last_id < 0
        ):
            raise ValueError
        return snapshot, kind, last_id
    except (ValueError, TypeError, KeyError, json.JSONDecodeError, binascii.Error) as exc:
        raise MCPServiceError("invalid_cursor", "Cursor is invalid for these export filters.", path="cursor") from exc


def _query_for_kind(kind, domain_ids, snapshot, updated_after, last_id):
    if kind == "domains":
        queryset = RVDomain.objects.filter(id__in=domain_ids)
    elif kind == "services":
        queryset = RVService.objects.filter(domain_id__in=domain_ids)
    elif kind == "items":
        queryset = RVItem.objects.select_related("domain", "service").filter(domain_id__in=domain_ids)
    elif kind == "media_manifest":
        queryset = RVMedia.objects.select_related("item", "item__domain").filter(item__domain_id__in=domain_ids)
    else:
        queryset = RVLink.objects.select_related("item", "item__domain").filter(item__domain_id__in=domain_ids)
    queryset = queryset.filter(id__gt=last_id, updated_at__lte=snapshot)
    if updated_after is not None:
        queryset = queryset.filter(updated_at__gt=updated_after)
    return queryset.order_by("id")


def _serialize_export(kind, record, client):
    if kind == "domains":
        value = serialize_domain(record)
    elif kind == "services":
        value = serialize_service(record)
    elif kind == "items":
        value = serialize_item(record, client)
    elif kind == "media_manifest":
        value = serialize_media(record)
    else:
        value = serialize_link(record)
    return {"kind": kind, "record": value}


def export_json_page(client, filters=None, cursor=None, limit=None):
    require_scope(client, "domain:owner")
    if limit is None:
        limit = settings.MCP_DEFAULT_PAGE_SIZE
    if not isinstance(limit, int) or limit < 1 or limit > settings.MCP_MAX_PAGE_SIZE:
        raise MCPServiceError(
            "limit_exceeded", f"limit must be between 1 and {settings.MCP_MAX_PAGE_SIZE}.", path="limit"
        )
    normalized = normalize_export_filters(client, filters)
    updated_after = _parse_export_time(normalized["updated_after"], "filters.updated_after")
    snapshot, cursor_kind, last_id = _decode_export_cursor(cursor, normalized)
    records = []
    for kind in normalized["kinds"]:
        if cursor_kind is not None and EXPORT_KINDS.index(kind) < EXPORT_KINDS.index(cursor_kind):
            continue
        kind_last_id = last_id if kind == cursor_kind else 0
        remaining = limit + 1 - len(records)
        if remaining <= 0:
            break
        queryset = _query_for_kind(
            kind, normalized["domain_ids"], snapshot, updated_after, kind_last_id
        )
        records.extend((kind, row) for row in queryset[:remaining])
    has_more = len(records) > limit
    records = records[:limit]
    next_cursor = None
    if has_more and records:
        final_kind, final_record = records[-1]
        next_cursor = _encode_export_cursor(snapshot, final_kind, final_record.id, normalized)
    return {
        "ok": True,
        "snapshot": snapshot.isoformat(),
        "filters": normalized,
        "records": [_serialize_export(kind, row, client) for kind, row in records],
        "next_cursor": next_cursor,
    }


def submit_export(client, domain_id, updated_after=None):
    require_scope(client, "domain:owner")
    domain = require_domain(client, domain_id)
    filters = normalize_export_filters(
        client,
        {"domain_ids": [domain.id], "updated_after": updated_after} if updated_after else {"domain_ids": [domain.id]},
    )
    job = enqueue_job(client, domain, "export_ndjson", {"filters": filters})
    audit(client, "submit_export", MCPAuditRecord.Outcome.SUCCESS, domain=domain, ids=[job.id])
    return job


def build_export_artifact(job, report):
    root, _data_root = _generated_root()
    folder = root / "exports" / str(job.domain_id)
    folder.mkdir(parents=True, exist_ok=True)
    if folder.is_symlink() or root not in folder.resolve().parents:
        raise MCPServiceError("unsafe_path", "Export directory is unsafe.")
    destination = folder / f"job-{job.id}-{secrets.token_hex(12)}.ndjson"
    temporary = None
    count = 0
    cursor = None
    digest = hashlib.sha256()
    try:
        with tempfile.NamedTemporaryFile(dir=folder, prefix=".export-", delete=False) as handle:
            temporary = Path(handle.name)
            os.chmod(temporary, 0o600)
            while True:
                page = export_json_page(
                    job.client,
                    job.payload.get("filters"),
                    cursor=cursor,
                    limit=settings.MCP_MAX_PAGE_SIZE,
                )
                for record in page["records"]:
                    line = json.dumps(record, ensure_ascii=False, separators=(",", ":"), sort_keys=True).encode("utf-8") + b"\n"
                    handle.write(line)
                    digest.update(line)
                    count += 1
                report(current=count, total=0)
                cursor = page["next_cursor"]
                if cursor is None:
                    break
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, destination)
    except Exception:
        if temporary is not None:
            temporary.unlink(missing_ok=True)
        raise
    job.artifact_path = str(destination.relative_to(root))
    job.artifact_sha256 = digest.hexdigest()
    job.artifact_size = destination.stat().st_size
    job.artifact_expires_at = timezone.now() + timedelta(seconds=settings.MCP_ARTIFACT_TTL_SECONDS)
    job.progress_total = count
    job.save(
        update_fields=[
            "artifact_path", "artifact_sha256", "artifact_size", "artifact_expires_at",
            "progress_total", "updated_at",
        ]
    )
    return {
        "record_count": count,
        "sha256": job.artifact_sha256,
        "size": job.artifact_size,
        "resumable": True,
    }
