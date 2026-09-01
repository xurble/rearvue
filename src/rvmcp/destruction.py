import hashlib
import hmac
import secrets
from datetime import timedelta

from django.conf import settings
from django.db import transaction
from django.db.models import F
from django.utils import timezone

from rvsite.models import RVDomain, RVItem, RVLink, RVMedia

from .capabilities import _delete_controlled_path
from .models import MCPAuditRecord, MCPDestructivePreview
from .services import (
    MCPServiceError,
    accessible_domain_ids,
    audit,
    canonical_hash,
    require_domain,
    require_scope,
)

DESTRUCTIVE_RESOURCES = frozenset({"links", "media", "items"})


def _token_hash(token):
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def _normalize_selector(resource, ids):
    if resource not in DESTRUCTIVE_RESOURCES:
        raise MCPServiceError("validation_error", "resource must be links, media, or items.", path="resource")
    if not isinstance(ids, list) or not ids:
        raise MCPServiceError("validation_error", "ids must be a non-empty list.", path="ids")
    if len(ids) > settings.MCP_MAX_DESTRUCTIVE_RECORDS:
        raise MCPServiceError("limit_exceeded", "Deletion exceeds the configured record limit.", path="ids")
    try:
        normalized = sorted({int(value) for value in ids})
    except (TypeError, ValueError) as exc:
        raise MCPServiceError("validation_error", "ids must contain integers.", path="ids") from exc
    if len(normalized) != len(ids) or any(value < 1 for value in normalized):
        raise MCPServiceError("validation_error", "ids must be unique positive integers.", path="ids")
    return {"resource": resource, "ids": normalized}


def _resource_queryset(resource, domain_id, ids):
    if resource == "links":
        return RVLink.objects.filter(id__in=ids, item__domain_id=domain_id)
    if resource == "media":
        return RVMedia.objects.filter(id__in=ids, item__domain_id=domain_id)
    return RVItem.objects.filter(id__in=ids, domain_id=domain_id)


def calculate_impact(domain_id, selector):
    resource = selector["resource"]
    ids = selector["ids"]
    rows = list(_resource_queryset(resource, domain_id, ids).order_by("id"))
    if len(rows) != len(ids):
        raise MCPServiceError("not_found", "One or more selected records were not found.", path="ids")
    impact = {
        "resource": resource,
        "record_count": len(rows),
        "records": [{"id": row.id, "revision": row.revision} for row in rows],
        "link_count": len(rows) if resource == "links" else 0,
        "media_count": len(rows) if resource == "media" else 0,
        "item_count": len(rows) if resource == "items" else 0,
        "controlled_file_count": 0,
        "poster_domain_ids": [],
    }
    if resource == "media":
        impact["controlled_file_count"] = sum(bool(row.original_media) for row in rows)
    elif resource == "items":
        media = list(RVMedia.objects.filter(item_id__in=ids).order_by("id"))
        links = list(RVLink.objects.filter(item_id__in=ids).order_by("id"))
        impact["media_count"] = len(media)
        impact["link_count"] = len(links)
        impact["controlled_file_count"] = sum(bool(row.original_media) for row in media)
        impact["media_records"] = [{"id": row.id, "revision": row.revision} for row in media]
        impact["link_records"] = [{"id": row.id, "revision": row.revision} for row in links]
        impact["poster_domain_ids"] = list(
            RVDomain.objects.filter(poster_image_id__in=ids).order_by("id").values_list("id", flat=True)
        )
    return impact


def preview_delete(client, domain_id, resource, ids):
    require_scope(client, "domain:owner")
    domain = require_domain(client, domain_id)
    selector = _normalize_selector(resource, ids)
    impact = calculate_impact(domain.id, selector)
    token = secrets.token_urlsafe(32)
    preview = MCPDestructivePreview.objects.create(
        client=client,
        domain=domain,
        operation=f"delete_{resource}",
        selector=selector,
        impact=impact,
        impact_hash=canonical_hash(impact),
        token_hash=_token_hash(token),
        expires_at=timezone.now() + timedelta(seconds=settings.MCP_DESTRUCTIVE_PREVIEW_TTL_SECONDS),
    )
    audit(
        client,
        "preview_delete",
        MCPAuditRecord.Outcome.SUCCESS,
        domain=domain,
        ids=selector["ids"],
        details={"resource": resource, "preview_id": preview.id, "record_count": impact["record_count"]},
    )
    return {
        "id": preview.id,
        "domain_id": domain.id,
        "operation": preview.operation,
        "impact": impact,
        "confirmation_token": token,
        "expires_at": preview.expires_at.isoformat(),
        "irreversible": True,
    }


def _cleanup_after_delete(audit_id, stored_paths):
    failures = []
    for index, stored_path in enumerate(stored_paths):
        error = _delete_controlled_path(stored_path)
        if error:
            failures.append({"index": index, "code": error})
    record = MCPAuditRecord.objects.filter(pk=audit_id).first()
    if record is not None:
        record.details = {**record.details, "cleanup_failures": failures}
        record.save(update_fields=["details"])


def confirm_delete(client, preview_id, confirmation_token):
    require_scope(client, "domain:owner")
    if not isinstance(confirmation_token, str) or not confirmation_token:
        raise MCPServiceError("validation_error", "confirmation_token is required.", path="confirmation_token")
    with transaction.atomic():
        preview = MCPDestructivePreview.objects.select_for_update().filter(
            pk=preview_id,
            client=client,
            domain_id__in=accessible_domain_ids(client),
        ).first()
        if preview is None:
            raise MCPServiceError("not_found", "Deletion preview not found.", path="preview_id")
        if preview.used_at is not None:
            raise MCPServiceError("confirmation_used", "Confirmation token has already been used.")
        if preview.expires_at <= timezone.now():
            raise MCPServiceError("confirmation_expired", "Confirmation token has expired.")
        if not hmac.compare_digest(preview.token_hash, _token_hash(confirmation_token)):
            audit(
                client,
                "confirm_delete",
                MCPAuditRecord.Outcome.FAILURE,
                domain=preview.domain,
                details={"code": "invalid_confirmation", "preview_id": preview.id},
            )
            raise MCPServiceError("invalid_confirmation", "Confirmation token is invalid.")
        current_impact = calculate_impact(preview.domain_id, preview.selector)
        if canonical_hash(current_impact) != preview.impact_hash or current_impact != preview.impact:
            audit(
                client,
                "confirm_delete",
                MCPAuditRecord.Outcome.CONFLICT,
                domain=preview.domain,
                ids=preview.selector["ids"],
                details={"code": "impact_changed", "preview_id": preview.id},
            )
            raise MCPServiceError("impact_changed", "Deletion impact changed; create a new preview.")

        resource = preview.selector["resource"]
        ids = preview.selector["ids"]
        if resource == "media":
            stored_paths = list(
                RVMedia.objects.filter(id__in=ids, item__domain_id=preview.domain_id)
                .order_by("id")
                .values_list("original_media", flat=True)
            )
        elif resource == "items":
            stored_paths = list(
                RVMedia.objects.filter(item_id__in=ids).order_by("id").values_list("original_media", flat=True)
            )
            RVDomain.objects.filter(poster_image_id__in=ids).update(
                poster_image=None,
                revision=F("revision") + 1,
                updated_at=timezone.now(),
            )
        else:
            stored_paths = []
        _resource_queryset(resource, preview.domain_id, ids).delete()
        preview.used_at = timezone.now()
        preview.save(update_fields=["used_at"])
        audit_record = MCPAuditRecord.objects.create(
            client=client,
            domain=preview.domain,
            domain_name=preview.domain.name,
            operation="confirm_delete",
            outcome=MCPAuditRecord.Outcome.SUCCESS,
            affected_ids=ids,
            affected_count=len(ids),
            details={
                "resource": resource,
                "preview_id": preview.id,
                "impact": current_impact,
                "cleanup_failures": [],
                "irreversible": True,
            },
        )
        transaction.on_commit(lambda: _cleanup_after_delete(audit_record.id, stored_paths))
    return {
        "ok": True,
        "deleted": {"resource": resource, "ids": ids, "count": len(ids)},
        "impact": current_impact,
        "irreversible": True,
    }
