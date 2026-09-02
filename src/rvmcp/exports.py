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
from django.db import connection, transaction
from django.db.models import F
from django.utils import timezone
from django.utils.dateparse import parse_datetime

from rvsite.models import RVDomain, RVItem, RVLink, RVMedia, RVService

from .capabilities import _generated_root, serialize_link, serialize_media
from .jobs import enqueue_job
from .models import MCPAuditRecord, MCPExportSnapshot, MCPExportSnapshotRecord
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


def _encode_export_cursor(snapshot_id, last_ordinal, binding):
    payload = {
        "v": 2,
        "snapshot_id": snapshot_id,
        "last_ordinal": last_ordinal,
        "binding": canonical_hash(binding),
    }
    return base64.urlsafe_b64encode(json.dumps(payload, separators=(",", ":")).encode()).decode().rstrip("=")


def _decode_export_cursor(cursor, binding):
    if not cursor:
        return None, 0
    try:
        padded = cursor + "=" * (-len(cursor) % 4)
        payload = json.loads(base64.urlsafe_b64decode(padded).decode())
        snapshot_id = int(payload["snapshot_id"])
        last_ordinal = int(payload["last_ordinal"])
        if (
            payload.get("v") != 2
            or payload.get("binding") != canonical_hash(binding)
            or snapshot_id < 1
            or last_ordinal < 0
        ):
            raise ValueError
        return snapshot_id, last_ordinal
    except (ValueError, TypeError, KeyError, json.JSONDecodeError, binascii.Error) as exc:
        raise MCPServiceError("invalid_cursor", "Cursor is invalid for these export filters.", path="cursor") from exc


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


def _load_bounded_snapshot_rows(queryset, loaded_count):
    limit = settings.MCP_MAX_EXPORT_SNAPSHOT_RECORDS
    remaining = limit - loaded_count
    rows = list(queryset[: max(0, remaining) + 1])
    if len(rows) > remaining:
        raise MCPServiceError(
            "limit_exceeded",
            (
                "Synchronous export exceeds the configured snapshot record limit; "
                "submit an asynchronous NDJSON export instead."
            ),
            path="filters",
        )
    return rows, loaded_count + len(rows)


def _materialize_export_snapshot(client, normalized, updated_after):
    domain_ids = normalized["domain_ids"]
    with transaction.atomic():
        MCPExportSnapshot.objects.filter(expires_at__lte=timezone.now()).delete()
        # The parent-first lock order prevents new services/items and dependent
        # media/links from entering the selected domains while their values are
        # materialized. Updates to existing rows are held until the snapshot is
        # complete, so later pages never query mutable source records.
        loaded_count = 0
        domains, loaded_count = _load_bounded_snapshot_rows(
            RVDomain.objects.select_for_update().filter(id__in=domain_ids).order_by("id"),
            loaded_count,
        )
        if connection.vendor == "sqlite":
            # SQLite ignores SELECT FOR UPDATE. Acquire its database write lock
            # before reading any mutable source rows so the materialization is
            # still internally consistent.
            RVDomain.objects.filter(id__in=domain_ids).update(revision=F("revision"))
        kinds = set(normalized["kinds"])
        services = []
        if "services" in kinds:
            services, loaded_count = _load_bounded_snapshot_rows(
                RVService.objects.select_for_update()
                .select_related("domain")
                .filter(domain_id__in=domain_ids)
                .order_by("id"),
                loaded_count,
            )
        items = []
        if kinds.intersection({"items", "media_manifest", "links"}):
            items, loaded_count = _load_bounded_snapshot_rows(
                RVItem.objects.select_for_update()
                .select_related("domain", "service")
                .filter(domain_id__in=domain_ids)
                .order_by("id"),
                loaded_count,
            )
        media = []
        if "media_manifest" in kinds:
            media, loaded_count = _load_bounded_snapshot_rows(
                RVMedia.objects.select_for_update()
                .select_related("item", "item__domain")
                .filter(item__domain_id__in=domain_ids)
                .order_by("id"),
                loaded_count,
            )
        links = []
        if "links" in kinds:
            links, loaded_count = _load_bounded_snapshot_rows(
                RVLink.objects.select_for_update()
                .select_related("item", "item__domain")
                .filter(item__domain_id__in=domain_ids)
                .order_by("id"),
                loaded_count,
            )
        rows_by_kind = {
            "domains": domains,
            "services": services,
            "items": items,
            "media_manifest": media,
            "links": links,
        }
        snapshot_at = timezone.now()
        snapshot = MCPExportSnapshot.objects.create(
            client=client,
            filters=normalized,
            binding_hash=canonical_hash(normalized),
            snapshot_at=snapshot_at,
            expires_at=snapshot_at + timedelta(seconds=settings.MCP_EXPORT_SNAPSHOT_TTL_SECONDS),
        )
        materialized = []
        ordinal = 0
        for kind in normalized["kinds"]:
            for record in rows_by_kind[kind]:
                if updated_after is not None and record.updated_at <= updated_after:
                    continue
                ordinal += 1
                materialized.append(
                    MCPExportSnapshotRecord(
                        snapshot=snapshot,
                        ordinal=ordinal,
                        kind=kind,
                        source_id=record.id,
                        payload=_serialize_export(kind, record, client),
                    )
                )
        MCPExportSnapshotRecord.objects.bulk_create(materialized, batch_size=500)
        return snapshot


def _load_export_snapshot(client, snapshot_id, normalized):
    snapshot = MCPExportSnapshot.objects.filter(
        pk=snapshot_id,
        client=client,
        binding_hash=canonical_hash(normalized),
        expires_at__gt=timezone.now(),
    ).first()
    if snapshot is None:
        raise MCPServiceError("invalid_cursor", "Cursor is invalid or expired.", path="cursor")
    return snapshot


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
    snapshot_id, last_ordinal = _decode_export_cursor(cursor, normalized)
    if snapshot_id is None:
        snapshot = _materialize_export_snapshot(client, normalized, updated_after)
    else:
        snapshot = _load_export_snapshot(client, snapshot_id, normalized)
    records = list(snapshot.records.filter(ordinal__gt=last_ordinal).order_by("ordinal")[: limit + 1])
    has_more = len(records) > limit
    records = records[:limit]
    next_cursor = None
    if has_more and records:
        next_cursor = _encode_export_cursor(snapshot.id, records[-1].ordinal, normalized)
    elif not has_more:
        snapshot.delete()
    return {
        "ok": True,
        "snapshot": snapshot.snapshot_at.isoformat(),
        "filters": normalized,
        "records": [record.payload for record in records],
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
