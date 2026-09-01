import base64
import hashlib
import hmac
import html
import json
import threading
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import timedelta
from typing import Any

import bleach
from django.conf import settings
from django.db import IntegrityError, transaction
from django.db.models import Exists, F, Max, Min, OuterRef, Q
from django.utils import timezone
from django.utils.dateparse import parse_datetime

from rvsite.models import RVDomain, RVItem, RVLink, RVMedia, RVService

from .models import MCPAuditRecord, MCPClient, MCPIdempotencyRecord

CAPTION_TAGS = frozenset({"a", "br", "blockquote", "code", "em", "li", "ol", "p", "pre", "strong", "ul"})
CAPTION_ATTRIBUTES = {"a": ["href", "title"]}
CAPTION_PROTOCOLS = frozenset({"http", "https", "mailto"})
WRITABLE_ITEM_FIELDS = frozenset(
    {"datetime_created", "remote_url", "title", "caption", "caption_format", "public", "moderated", "edited", "raw_data"}
)
_operation_locks = {}
_operation_locks_guard = threading.Lock()


@contextmanager
def operation_lock(key):
    with _operation_locks_guard:
        lock, references = _operation_locks.get(key, (threading.Lock(), 0))
        _operation_locks[key] = (lock, references + 1)
    lock.acquire()
    try:
        yield
    finally:
        lock.release()
        with _operation_locks_guard:
            current_lock, references = _operation_locks[key]
            if references == 1:
                del _operation_locks[key]
            else:
                _operation_locks[key] = (current_lock, references - 1)


@dataclass
class MCPServiceError(Exception):
    code: str
    message: str
    path: str | None = None
    retryable: bool = False
    details: dict[str, Any] | None = None

    def as_result(self):
        error = {"code": self.code, "message": self.message, "retryable": self.retryable}
        if self.path:
            error["path"] = self.path
        if self.details:
            error["details"] = self.details
        return {"ok": False, "error": error}


def authenticate_token(token):
    if not token or not token.startswith("rvmcp_"):
        return None
    parts = token.split("_", 2)
    if len(parts) != 3 or not parts[1] or not parts[2]:
        return None
    digest = MCPClient.hash_token(token)
    for client in MCPClient.objects.filter(token_prefix=parts[1]):
        if client.is_active and hmac.compare_digest(client.token_hash, digest):
            return client
    return None


def require_scope(client, scope):
    if "domain:owner" not in client.scopes:
        raise MCPServiceError("forbidden", "The domain owner capability is required.")


def accessible_domain_ids(client):
    return client.domains.values_list("id", flat=True)


def require_domain(client, domain_id, path="domain_id"):
    domain = client.domains.filter(pk=domain_id).first()
    if domain is None:
        # Deliberately do not distinguish nonexistent from inaccessible domains.
        raise MCPServiceError("not_found", "Domain not found.", path=path)
    return domain


def serialize_domain(domain):
    return {
        "id": domain.id,
        "name": domain.name,
        "display_name": domain.display_name,
        "public_origin": domain.public_origin,
        "blurb": domain.blurb or "",
        "min_year": domain.min_year,
        "max_year": domain.max_year,
        "last_updated": domain.last_updated.isoformat() if domain.last_updated else None,
        "updated_at": domain.updated_at.isoformat(),
        "revision": domain.revision,
    }


def serialize_service(service, item_count=None):
    result = {
        "id": service.id,
        "domain_id": service.domain_id,
        "name": service.name,
        "type": service.type,
        "live": service.live,
        "hide_unmoderated": service.hide_unmoderated,
        "last_checked": service.last_checked.isoformat() if service.last_checked else None,
        "updated_at": service.updated_at.isoformat(),
        "revision": service.revision,
    }
    if item_count is not None:
        result["item_count"] = item_count
    return result


def serialize_item(item, client, include_media=False, include_links=False):
    result = {
        "id": item.id,
        "domain_id": item.domain_id,
        "service_id": item.service_id,
        "item_id": item.item_id,
        "slug": item.slug,
        "date_retrieved": item.date_retrieved.isoformat(),
        "date_created": item.date_created.isoformat(),
        "datetime_created": item.datetime_created.isoformat(),
        "updated_at": item.updated_at.isoformat(),
        "revision": item.revision,
        "remote_url": item.remote_url,
        "title": item.title,
        "caption": item.caption,
        "caption_format": "html",
        "public": item.public,
        "moderated": item.moderated,
        "edited": item.edited,
        "mirror_state": item.mirror_state,
    }
    if "domain:owner" in client.scopes:
        try:
            result["raw_data"] = json.loads(item.raw_data) if item.raw_data else None
        except json.JSONDecodeError:
            result["raw_data"] = None
            result["raw_data_warning"] = "Stored legacy raw_data is not valid JSON."
    if include_media:
        result["media"] = [
            {
                "id": media.id,
                "media_type": media.media_type,
                "medium": media.medium,
                "mime_type": media.mime_type,
                "updated_at": media.updated_at.isoformat(),
                "revision": media.revision,
                "download_url": f"/mcp-download/media/{media.id}/",
            }
            for media in item.rvmedia_set.all()
        ]
    if include_links:
        result["links"] = [
            {
                "id": link.id,
                "url": link.url,
                "title": link.title,
                "description": link.description,
                "is_context": link.is_context,
                "updated_at": link.updated_at.isoformat(),
                "revision": link.revision,
            }
            for link in item.rvlink_set.all()
        ]
    return result


def sanitize_caption(value, caption_format="plain"):
    if not isinstance(value, str):
        raise MCPServiceError("validation_error", "Caption must be a string.", path="caption")
    if caption_format == "plain":
        return html.escape(value).replace("\n", "<br>")
    if caption_format == "html":
        return bleach.clean(
            value,
            tags=CAPTION_TAGS,
            attributes=CAPTION_ATTRIBUTES,
            protocols=CAPTION_PROTOCOLS,
            strip=True,
        )
    raise MCPServiceError(
        "validation_error",
        "caption_format must be 'plain' or 'html'.",
        path="caption_format",
    )


def normalize_raw_data(value):
    if value is None:
        return ""
    try:
        encoded = json.dumps(value, ensure_ascii=False, separators=(",", ":"), sort_keys=True)
    except (TypeError, ValueError) as exc:
        raise MCPServiceError("validation_error", "raw_data must be valid JSON.", path="raw_data") from exc
    if len(encoded.encode("utf-8")) > settings.MCP_MAX_RAW_DATA_BYTES:
        raise MCPServiceError(
            "limit_exceeded",
            f"raw_data exceeds the {settings.MCP_MAX_RAW_DATA_BYTES}-byte limit.",
            path="raw_data",
        )
    return encoded


def parse_created_datetime(value, path="datetime_created"):
    if not isinstance(value, str):
        raise MCPServiceError("validation_error", "Expected an ISO 8601 datetime string.", path=path)
    parsed = parse_datetime(value)
    if parsed is None or timezone.is_naive(parsed):
        raise MCPServiceError("validation_error", "Datetime must be ISO 8601 with a timezone.", path=path)
    return parsed


def validate_item_payload(payload, creating=False):
    if not isinstance(payload, dict):
        raise MCPServiceError("validation_error", "Item must be an object.", path="item")
    allowed = WRITABLE_ITEM_FIELDS | ({"service_id", "item_id", "expected_revision"} if creating else {"expected_revision"})
    unknown = sorted(set(payload) - allowed)
    if unknown:
        raise MCPServiceError("validation_error", f"Unknown fields: {', '.join(unknown)}.", path=unknown[0])
    if creating:
        for field in ("service_id", "item_id", "datetime_created"):
            if field not in payload:
                raise MCPServiceError("validation_error", "Field is required.", path=field)
        if not isinstance(payload["item_id"], str) or not payload["item_id"] or len(payload["item_id"]) > 128:
            raise MCPServiceError("validation_error", "item_id must be 1–128 characters.", path="item_id")


def item_values(payload):
    values = {}
    if "datetime_created" in payload:
        created = parse_created_datetime(payload["datetime_created"])
        values["datetime_created"] = created
        values["date_created"] = created.date()
    for field in ("remote_url", "title"):
        if field in payload:
            value = payload[field]
            limit = 512
            if not isinstance(value, str) or len(value) > limit:
                raise MCPServiceError("validation_error", f"{field} must be a string of at most {limit} characters.", path=field)
            values[field] = value
    for field in ("public", "moderated", "edited"):
        if field in payload:
            if not isinstance(payload[field], bool):
                raise MCPServiceError("validation_error", f"{field} must be boolean.", path=field)
            values[field] = payload[field]
    if "caption" in payload or "caption_format" in payload:
        if "caption" not in payload:
            raise MCPServiceError("validation_error", "caption is required when caption_format is supplied.", path="caption")
        values["caption"] = sanitize_caption(payload["caption"], payload.get("caption_format", "plain"))
    if "raw_data" in payload:
        values["raw_data"] = normalize_raw_data(payload["raw_data"])
    return values


def refresh_domain_metadata(domain_id):
    bounds = RVItem.objects.filter(domain_id=domain_id).aggregate(
        min_year=Min("date_created__year"), max_year=Max("date_created__year")
    )
    RVDomain.objects.filter(pk=domain_id).update(
        min_year=bounds["min_year"] or 0,
        max_year=bounds["max_year"] or 0,
        last_updated=timezone.now(),
        revision=F("revision") + 1,
        updated_at=timezone.now(),
    )


def audit(client, operation, outcome, domain=None, ids=None, idempotency_key="", details=None):
    MCPAuditRecord.objects.create(
        client=client,
        domain=domain,
        domain_name=domain.name if domain else "",
        operation=operation,
        outcome=outcome,
        affected_ids=ids or [],
        affected_count=len(ids or []),
        idempotency_key=idempotency_key,
        details=details or {},
    )


def _service_for_write(client, service_id):
    try:
        service_id = int(service_id)
    except (TypeError, ValueError) as exc:
        raise MCPServiceError("validation_error", "service_id must be an integer.", path="service_id") from exc
    service = RVService.objects.select_related("domain").filter(pk=service_id, domain_id__in=accessible_domain_ids(client)).first()
    if service is None:
        raise MCPServiceError("not_found", "Service not found.", path="service_id")
    return service


def _domain_for_write_payload(client, payload):
    if not isinstance(payload, dict):
        return None
    try:
        service_id = int(payload.get("service_id"))
    except (TypeError, ValueError):
        return None
    service = RVService.objects.select_related("domain").filter(
        pk=service_id, domain_id__in=accessible_domain_ids(client)
    ).first()
    return service.domain if service else None


def _audit_error(client, operation, error, domain=None):
    outcome = (
        MCPAuditRecord.Outcome.CONFLICT
        if error.code in {"identity_conflict", "expected_revision_required", "revision_conflict", "idempotency_conflict"}
        else MCPAuditRecord.Outcome.FAILURE
    )
    details = {"code": error.code}
    if error.path:
        details["path"] = error.path
    audit(client, operation, outcome, domain=domain, details=details)


def _create_item(client, payload):
    require_scope(client, "items:write")
    validate_item_payload(payload, creating=True)
    service = _service_for_write(client, payload["service_id"])
    try:
        with transaction.atomic():
            item = RVItem.objects.create(
                service=service,
                domain=service.domain,
                item_id=payload["item_id"],
                **item_values(payload),
            )
    except IntegrityError as exc:
        raise MCPServiceError(
            "identity_conflict",
            "An item with this service_id and item_id already exists.",
            retryable=False,
        ) from exc
    refresh_domain_metadata(service.domain_id)
    return item


def create_item(client, payload, *, operation="create_item", write_audit=True):
    try:
        item = _create_item(client, payload)
    except MCPServiceError as exc:
        if write_audit:
            _audit_error(client, operation, exc, domain=_domain_for_write_payload(client, payload))
        raise
    if write_audit:
        audit(client, operation, MCPAuditRecord.Outcome.SUCCESS, domain=item.domain, ids=[item.id])
    return item


def _item_for_client(client, item_id):
    try:
        item_id = int(item_id)
    except (TypeError, ValueError) as exc:
        raise MCPServiceError("validation_error", "item id must be an integer.", path="id") from exc
    item = RVItem.objects.select_related("domain", "service").filter(pk=item_id, domain_id__in=accessible_domain_ids(client)).first()
    if item is None:
        raise MCPServiceError("not_found", "Item not found.", path="id")
    return item


def _update_item(client, item_id, payload):
    require_scope(client, "items:write")
    validate_item_payload(payload, creating=False)
    if "expected_revision" not in payload or not isinstance(payload["expected_revision"], int):
        raise MCPServiceError("validation_error", "expected_revision is required and must be an integer.", path="expected_revision")
    item = _item_for_client(client, item_id)
    values = item_values(payload)
    if not values:
        raise MCPServiceError("validation_error", "At least one writable field is required.", path="item")
    values.update(revision=F("revision") + 1, updated_at=timezone.now())
    changed = RVItem.objects.filter(pk=item.pk, revision=payload["expected_revision"]).update(**values)
    if not changed:
        current = RVItem.objects.only("revision").get(pk=item.pk)
        raise MCPServiceError(
            "revision_conflict",
            "The item changed after the supplied revision.",
            details={"current_revision": current.revision},
        )
    item.refresh_from_db()
    refresh_domain_metadata(item.domain_id)
    return item


def update_item(client, item_id, payload, *, operation="update_item", write_audit=True):
    domain = None
    try:
        item = _update_item(client, item_id, payload)
        domain = item.domain
    except MCPServiceError as exc:
        if write_audit:
            try:
                audit_item_id = int(item_id)
            except (TypeError, ValueError):
                audit_item_id = None
            accessible_item = (
                RVItem.objects.select_related("domain")
                .filter(pk=audit_item_id, domain_id__in=accessible_domain_ids(client))
                .first()
                if audit_item_id is not None
                else None
            )
            _audit_error(client, operation, exc, domain=accessible_item.domain if accessible_item else None)
        raise
    if write_audit:
        audit(client, operation, MCPAuditRecord.Outcome.SUCCESS, domain=domain, ids=[item.id])
    return item


def _upsert_item(client, payload):
    require_scope(client, "items:write")
    validate_item_payload(payload, creating=True)
    with operation_lock(("upsert", int(payload["service_id"]), payload["item_id"])):
        service = _service_for_write(client, payload["service_id"])
        existing = RVItem.objects.filter(service=service, item_id=payload["item_id"]).first()
        if existing is None:
            try:
                return _create_item(client, payload), True
            except MCPServiceError as exc:
                if exc.code != "identity_conflict":
                    raise
                # A writer in another process may have committed after our read.
                existing = RVItem.objects.filter(service=service, item_id=payload["item_id"]).first()
                if existing is None:
                    raise

        desired = item_values(payload)
        unchanged = all(getattr(existing, key) == value for key, value in desired.items())
        if unchanged:
            return existing, False
        if "expected_revision" not in payload:
            raise MCPServiceError(
                "expected_revision_required",
                "expected_revision is required when an upsert would change an existing item.",
                details={"current_revision": existing.revision},
            )
        update_payload = {key: value for key, value in payload.items() if key not in {"service_id", "item_id"}}
        return _update_item(client, existing.id, update_payload), False


def upsert_item(client, payload, *, operation="upsert_item", write_audit=True):
    try:
        item, created = _upsert_item(client, payload)
    except MCPServiceError as exc:
        if write_audit:
            _audit_error(client, operation, exc, domain=_domain_for_write_payload(client, payload))
        raise
    if write_audit:
        audit(
            client,
            operation,
            MCPAuditRecord.Outcome.SUCCESS,
            domain=item.domain,
            ids=[item.id],
            details={"created": created},
        )
    return item, created


def canonical_hash(value):
    encoded = json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def idempotent_result(client, operation, key, request, callback):
    if not isinstance(key, str) or not key or len(key) > 128:
        raise MCPServiceError("validation_error", "idempotency_key must be 1–128 characters.", path="idempotency_key")
    request_hash = canonical_hash(request)
    with operation_lock(("idempotency", client.id, operation, key)), transaction.atomic():
        existing = MCPIdempotencyRecord.objects.select_for_update().filter(
            client=client, operation=operation, key=key
        ).first()
        if existing and existing.expires_at > timezone.now():
            if existing.request_hash != request_hash:
                raise MCPServiceError("idempotency_conflict", "The idempotency key was already used with different input.")
            return existing.response
        if existing:
            existing.delete()

        try:
            with transaction.atomic():
                claimed = MCPIdempotencyRecord.objects.create(
                    client=client,
                    operation=operation,
                    key=key,
                    request_hash=request_hash,
                    response={},
                    expires_at=timezone.now() + timedelta(seconds=settings.MCP_IDEMPOTENCY_TTL_SECONDS),
                )
        except IntegrityError:
            winner = MCPIdempotencyRecord.objects.get(client=client, operation=operation, key=key)
            if winner.request_hash != request_hash:
                raise MCPServiceError("idempotency_conflict", "The idempotency key was already used with different input.")
            return winner.response

        # Claiming and side effects commit together. A concurrent claimant blocks
        # on the unique key and can only observe the completed winner response.
        response = callback()
        claimed.response = response
        claimed.save(update_fields=["response"])
        return response


def bulk_upsert_items(client, records, idempotency_key):
    try:
        require_scope(client, "items:write")
        if not isinstance(records, list) or not records:
            raise MCPServiceError("validation_error", "items must be a non-empty list.", path="items")
        if len(records) > settings.MCP_MAX_BULK_ITEMS:
            raise MCPServiceError("limit_exceeded", f"A bulk request may contain at most {settings.MCP_MAX_BULK_ITEMS} items.", path="items")
    except MCPServiceError as exc:
        _audit_error(client, "bulk_upsert_items", exc)
        raise

    def execute():
        results = []
        affected_ids = []
        domain_ids = {
            domain.id
            for record in records
            if (domain := _domain_for_write_payload(client, record)) is not None
        }
        for index, record in enumerate(records):
            try:
                with transaction.atomic():
                    item, created = upsert_item(client, record, operation="bulk_upsert_items", write_audit=False)
                results.append({"index": index, "ok": True, "created": created, "item": serialize_item(item, client)})
                affected_ids.append(item.id)
            except MCPServiceError as exc:
                error = exc.as_result()["error"]
                error["path"] = f"items[{index}]" + (f".{error['path']}" if error.get("path") else "")
                results.append({"index": index, "ok": False, "error": error})
        succeeded = len(affected_ids)
        outcome = MCPAuditRecord.Outcome.SUCCESS if succeeded == len(records) else (MCPAuditRecord.Outcome.PARTIAL if succeeded else MCPAuditRecord.Outcome.FAILURE)
        audit(
            client,
            "bulk_upsert_items",
            outcome,
            domain=RVDomain.objects.filter(pk=next(iter(domain_ids))).first() if len(domain_ids) == 1 else None,
            ids=affected_ids,
            idempotency_key=idempotency_key,
            details={"submitted_count": len(records), "succeeded_count": succeeded, "failed_count": len(records) - succeeded, "domain_ids": sorted(domain_ids)},
        )
        return {"ok": True, "submitted_count": len(records), "succeeded_count": succeeded, "failed_count": len(records) - succeeded, "results": results}

    try:
        return idempotent_result(client, "bulk_upsert_items", idempotency_key, records, execute)
    except MCPServiceError as exc:
        _audit_error(client, "bulk_upsert_items", exc)
        raise


def encode_cursor(item, filter_hash):
    payload = {"v": 1, "datetime_created": item.datetime_created.isoformat(), "id": item.id, "filter_hash": filter_hash}
    return base64.urlsafe_b64encode(json.dumps(payload, separators=(",", ":")).encode()).decode().rstrip("=")


def decode_cursor(cursor, filter_hash):
    try:
        padded = cursor + "=" * (-len(cursor) % 4)
        payload = json.loads(base64.urlsafe_b64decode(padded).decode())
        created = parse_created_datetime(payload["datetime_created"], path="cursor")
        item_id = int(payload["id"])
        if payload.get("v") != 1 or payload.get("filter_hash") != filter_hash:
            raise ValueError
    except (KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
        raise MCPServiceError("invalid_cursor", "Cursor is invalid or does not match these filters.", path="cursor") from exc
    return created, item_id


def search_items(client, filters=None, cursor=None, limit=None, include_media=False, include_links=False):
    require_scope(client, "items:read")
    filters = filters or {}
    if not isinstance(filters, dict):
        raise MCPServiceError("validation_error", "filters must be an object.", path="filters")
    allowed = {"domain_ids", "service_ids", "service_types", "item_id", "created_from", "created_to", "retrieved_from", "retrieved_to", "text", "remote_url", "public", "moderated", "edited", "mirror_state", "has_media", "media_types", "has_links", "link_is_context"}
    unknown = sorted(set(filters) - allowed)
    if unknown:
        raise MCPServiceError("validation_error", f"Unknown filters: {', '.join(unknown)}.", path=f"filters.{unknown[0]}")
    try:
        limit = settings.MCP_DEFAULT_PAGE_SIZE if limit is None else int(limit)
    except (TypeError, ValueError) as exc:
        raise MCPServiceError("validation_error", "limit must be an integer.", path="limit") from exc
    if limit < 1 or limit > settings.MCP_MAX_PAGE_SIZE:
        raise MCPServiceError("limit_exceeded", f"limit must be between 1 and {settings.MCP_MAX_PAGE_SIZE}.", path="limit")

    queryset = RVItem.objects.select_related("domain", "service").filter(domain_id__in=accessible_domain_ids(client))
    requested_domains = filters.get("domain_ids")
    if requested_domains is not None:
        if not isinstance(requested_domains, list):
            raise MCPServiceError("validation_error", "domain_ids must be a list.", path="filters.domain_ids")
        allowed_domains = set(accessible_domain_ids(client))
        try:
            requested_set = {int(value) for value in requested_domains}
        except (TypeError, ValueError) as exc:
            raise MCPServiceError("validation_error", "domain_ids must contain integers.", path="filters.domain_ids") from exc
        if not requested_set.issubset(allowed_domains):
            raise MCPServiceError("not_found", "One or more domains were not found.", path="filters.domain_ids")
        queryset = queryset.filter(domain_id__in=requested_set)
    if "service_ids" in filters:
        queryset = queryset.filter(service_id__in=filters["service_ids"])
    if "service_types" in filters:
        queryset = queryset.filter(service__type__in=filters["service_types"])
    for field in ("item_id", "public", "moderated", "edited", "mirror_state"):
        if field in filters:
            queryset = queryset.filter(**{field: filters[field]})
    if "created_from" in filters:
        queryset = queryset.filter(datetime_created__gte=parse_created_datetime(filters["created_from"], "filters.created_from"))
    if "created_to" in filters:
        queryset = queryset.filter(datetime_created__lte=parse_created_datetime(filters["created_to"], "filters.created_to"))
    if "retrieved_from" in filters:
        queryset = queryset.filter(date_retrieved__gte=parse_created_datetime(filters["retrieved_from"], "filters.retrieved_from"))
    if "retrieved_to" in filters:
        queryset = queryset.filter(date_retrieved__lte=parse_created_datetime(filters["retrieved_to"], "filters.retrieved_to"))
    if "text" in filters:
        queryset = queryset.filter(Q(title__icontains=filters["text"]) | Q(caption__icontains=filters["text"]))
    if "remote_url" in filters:
        queryset = queryset.filter(remote_url__icontains=filters["remote_url"])
    if "has_media" in filters or "media_types" in filters:
        media = RVMedia.objects.filter(item_id=OuterRef("pk"))
        if "media_types" in filters:
            media = media.filter(media_type__in=filters["media_types"])
        queryset = queryset.annotate(matches_media=Exists(media)).filter(matches_media=filters.get("has_media", True))
    if "has_links" in filters or "link_is_context" in filters:
        links = RVLink.objects.filter(item_id=OuterRef("pk"))
        if "link_is_context" in filters:
            links = links.filter(is_context=filters["link_is_context"])
        queryset = queryset.annotate(matches_links=Exists(links)).filter(matches_links=filters.get("has_links", True))

    filter_hash = canonical_hash({"filters": filters, "include_media": include_media, "include_links": include_links})
    if cursor:
        created, item_id = decode_cursor(cursor, filter_hash)
        queryset = queryset.filter(Q(datetime_created__lt=created) | Q(datetime_created=created, id__lt=item_id))
    if include_media:
        queryset = queryset.prefetch_related("rvmedia_set")
    if include_links:
        queryset = queryset.prefetch_related("rvlink_set")
    page = list(queryset.order_by("-datetime_created", "-id")[: limit + 1])
    has_more = len(page) > limit
    page = page[:limit]
    return {
        "ok": True,
        "items": [serialize_item(item, client, include_media=include_media, include_links=include_links) for item in page],
        "next_cursor": encode_cursor(page[-1], filter_hash) if has_more and page else None,
    }
