import base64
import binascii
import io
import json
import os
import secrets
import tempfile
from pathlib import Path

from bs4 import BeautifulSoup
from django.conf import settings
from django.db import transaction
from django.db.models import F
from django.utils import timezone
from PIL import Image, UnidentifiedImageError

from rearvue.utils import get_public_url, validate_public_http_url
from rvsite.models import RVItem, RVLink, RVMedia, RVService

from .jobs import enqueue_job
from .models import MCPAuditRecord, MCPJob
from .services import (
    MCPServiceError,
    _item_for_client,
    accessible_domain_ids,
    audit,
    canonical_hash,
    require_domain,
    require_scope,
    serialize_service,
)

SUPPORTED_MEDIA = (
    ("jpeg", "image/jpeg", "jpg", 1),
    ("png", "image/png", "png", 1),
    ("webp", "image/webp", "webp", 1),
    ("gif", "image/gif", "gif", 1),
    ("mp4", "video/mp4", "mp4", 2),
)


def _bounded_string(value, path, limit, *, required=False):
    if not isinstance(value, str) or (required and not value.strip()) or len(value) > limit:
        qualifier = "non-empty " if required else ""
        raise MCPServiceError(
            "validation_error",
            f"{path} must be a {qualifier}string of at most {limit} characters.",
            path=path,
        )
    return value.strip() if required else value


def _expected_revision(payload):
    revision = payload.get("expected_revision") if isinstance(payload, dict) else None
    if not isinstance(revision, int) or revision < 1:
        raise MCPServiceError(
            "validation_error",
            "expected_revision is required and must be a positive integer.",
            path="expected_revision",
        )
    return revision


def _page_limit(limit):
    if limit is None:
        return settings.MCP_DEFAULT_PAGE_SIZE
    if not isinstance(limit, int) or limit < 1 or limit > settings.MCP_MAX_PAGE_SIZE:
        raise MCPServiceError(
            "limit_exceeded",
            f"limit must be between 1 and {settings.MCP_MAX_PAGE_SIZE}.",
            path="limit",
        )
    return limit


def _encode_id_cursor(last_id, binding):
    payload = {"v": 1, "last_id": last_id, "binding": canonical_hash(binding)}
    return base64.urlsafe_b64encode(json.dumps(payload, separators=(",", ":")).encode()).decode().rstrip("=")


def _decode_id_cursor(cursor, binding):
    if not cursor:
        return 0
    try:
        padded = cursor + "=" * (-len(cursor) % 4)
        payload = json.loads(base64.urlsafe_b64decode(padded).decode())
        if payload.get("v") != 1 or payload.get("binding") != canonical_hash(binding):
            raise ValueError
        last_id = int(payload["last_id"])
        if last_id < 0:
            raise ValueError
        return last_id
    except (ValueError, TypeError, KeyError, json.JSONDecodeError, binascii.Error) as exc:
        raise MCPServiceError("invalid_cursor", "Cursor is invalid for this request.", path="cursor") from exc


def serialize_link(link):
    image_url = link.image if link.image.startswith(("https://", "http://")) else ""
    return {
        "id": link.id,
        "item_id": link.item_id,
        "domain_id": link.item.domain_id,
        "url": link.url,
        "title": link.title,
        "description": link.description,
        "image_url": image_url,
        "is_context": link.is_context,
        "revision": link.revision,
        "updated_at": link.updated_at.isoformat(),
    }


def _link_for_client(client, link_id, *, lock=False):
    queryset = RVLink.objects.select_related("item", "item__domain")
    if lock:
        queryset = queryset.select_for_update()
    try:
        link_id = int(link_id)
    except (TypeError, ValueError) as exc:
        raise MCPServiceError("validation_error", "link_id must be an integer.", path="link_id") from exc
    link = queryset.filter(pk=link_id, item__domain_id__in=accessible_domain_ids(client)).first()
    if link is None:
        raise MCPServiceError("not_found", "Link not found.", path="link_id")
    return link


def list_links(client, *, domain_id=None, item_id=None, cursor=None, limit=None):
    require_scope(client, "domain:owner")
    limit = _page_limit(limit)
    binding = {"domain_id": domain_id, "item_id": item_id}
    queryset = RVLink.objects.select_related("item", "item__domain").filter(
        item__domain_id__in=accessible_domain_ids(client)
    )
    if domain_id is not None:
        require_domain(client, domain_id)
        queryset = queryset.filter(item__domain_id=domain_id)
    if item_id is not None:
        item = _item_for_client(client, item_id)
        queryset = queryset.filter(item=item)
    queryset = queryset.filter(id__gt=_decode_id_cursor(cursor, binding)).order_by("id")
    page = list(queryset[: limit + 1])
    has_more = len(page) > limit
    page = page[:limit]
    return {
        "ok": True,
        "links": [serialize_link(link) for link in page],
        "next_cursor": _encode_id_cursor(page[-1].id, binding) if has_more else None,
    }


def get_link(client, link_id):
    require_scope(client, "domain:owner")
    return serialize_link(_link_for_client(client, link_id))


def _validate_link_payload(payload, *, creating):
    if not isinstance(payload, dict):
        raise MCPServiceError("validation_error", "Link must be an object.", path="link")
    allowed = {"url", "title", "description", "is_context"}
    if creating:
        allowed.add("item_id")
    else:
        allowed.add("expected_revision")
    unknown = sorted(set(payload) - allowed)
    if unknown:
        raise MCPServiceError("validation_error", f"Unknown fields: {', '.join(unknown)}.", path=unknown[0])
    if creating and "item_id" not in payload:
        raise MCPServiceError("validation_error", "Field is required.", path="item_id")
    if creating and "url" not in payload:
        raise MCPServiceError("validation_error", "Field is required.", path="url")
    values = {}
    if "url" in payload:
        url = _bounded_string(payload["url"], "url", 512, required=True)
        if not validate_public_http_url(url):
            raise MCPServiceError("unsafe_url", "URL must resolve to a public HTTP(S) address.", path="url")
        values["url"] = url
    if "title" in payload:
        values["title"] = _bounded_string(payload["title"], "title", 512)
    if "description" in payload:
        values["description"] = _bounded_string(payload["description"], "description", 10_000)
    if "is_context" in payload:
        if not isinstance(payload["is_context"], bool):
            raise MCPServiceError("validation_error", "is_context must be boolean.", path="is_context")
        values["is_context"] = payload["is_context"]
    return values


def create_link(client, payload):
    require_scope(client, "domain:owner")
    values = _validate_link_payload(payload, creating=True)
    item = _item_for_client(client, payload["item_id"])
    link = RVLink.objects.create(item=item, **values)
    audit(client, "create_link", MCPAuditRecord.Outcome.SUCCESS, domain=item.domain, ids=[link.id])
    return link


def update_link(client, link_id, payload):
    require_scope(client, "domain:owner")
    revision = _expected_revision(payload)
    values = _validate_link_payload(payload, creating=False)
    if not values:
        raise MCPServiceError("validation_error", "At least one writable field is required.", path="link")
    link = _link_for_client(client, link_id)
    updated = RVLink.objects.filter(pk=link.pk, revision=revision).update(
        **values, revision=F("revision") + 1, updated_at=timezone.now()
    )
    if not updated:
        current = RVLink.objects.only("revision").get(pk=link.pk)
        raise MCPServiceError(
            "revision_conflict", "The link changed after the supplied revision.",
            details={"current_revision": current.revision},
        )
    link.refresh_from_db()
    audit(client, "update_link", MCPAuditRecord.Outcome.SUCCESS, domain=link.item.domain, ids=[link.id])
    return link


def serialize_media(media):
    return {
        "id": media.id,
        "item_id": media.item_id,
        "domain_id": media.item.domain_id,
        "media_type": media.media_type,
        "medium": media.medium,
        "mime_type": media.mime_type,
        "revision": media.revision,
        "updated_at": media.updated_at.isoformat(),
        "download_url": f"/mcp-download/media/{media.id}/",
    }


def _media_for_client(client, media_id, *, lock=False):
    queryset = RVMedia.objects.select_related("item", "item__domain")
    if lock:
        queryset = queryset.select_for_update()
    try:
        media_id = int(media_id)
    except (TypeError, ValueError) as exc:
        raise MCPServiceError("validation_error", "media_id must be an integer.", path="media_id") from exc
    media = queryset.filter(pk=media_id, item__domain_id__in=accessible_domain_ids(client)).first()
    if media is None:
        raise MCPServiceError("not_found", "Media not found.", path="media_id")
    return media


def list_media(client, *, domain_id=None, item_id=None, cursor=None, limit=None):
    require_scope(client, "domain:owner")
    limit = _page_limit(limit)
    binding = {"domain_id": domain_id, "item_id": item_id}
    queryset = RVMedia.objects.select_related("item", "item__domain").filter(
        item__domain_id__in=accessible_domain_ids(client)
    )
    if domain_id is not None:
        require_domain(client, domain_id)
        queryset = queryset.filter(item__domain_id=domain_id)
    if item_id is not None:
        item = _item_for_client(client, item_id)
        queryset = queryset.filter(item=item)
    queryset = queryset.filter(id__gt=_decode_id_cursor(cursor, binding)).order_by("id")
    page = list(queryset[: limit + 1])
    has_more = len(page) > limit
    page = page[:limit]
    return {
        "ok": True,
        "media": [serialize_media(media) for media in page],
        "next_cursor": _encode_id_cursor(page[-1].id, binding) if has_more else None,
    }


def get_media(client, media_id):
    require_scope(client, "domain:owner")
    return serialize_media(_media_for_client(client, media_id))


def _read_media_source(payload):
    if not isinstance(payload, dict):
        raise MCPServiceError("validation_error", "Media must be an object.", path="media")
    sources = [name for name in ("content_base64", "source_url") if payload.get(name) is not None]
    if len(sources) != 1:
        raise MCPServiceError(
            "validation_error", "Supply exactly one of content_base64 or source_url.", path="media"
        )
    if sources[0] == "content_base64":
        encoded = payload["content_base64"]
        if not isinstance(encoded, str):
            raise MCPServiceError("validation_error", "content_base64 must be a string.", path="content_base64")
        if len(encoded) > ((settings.MCP_MAX_MEDIA_BYTES + 2) // 3) * 4 + 8:
            raise MCPServiceError("limit_exceeded", "Encoded media exceeds the configured limit.", path="content_base64")
        try:
            content = base64.b64decode(encoded, validate=True)
        except (ValueError, binascii.Error) as exc:
            raise MCPServiceError("validation_error", "content_base64 is invalid.", path="content_base64") from exc
    else:
        url = _bounded_string(payload["source_url"], "source_url", 2048, required=True)
        if not validate_public_http_url(url):
            raise MCPServiceError("unsafe_url", "URL must resolve to a public HTTP(S) address.", path="source_url")
        try:
            response = get_public_url(url, timeout=30, stream=True)
            try:
                response.raise_for_status()
                declared = response.headers.get("Content-Length")
                if declared and int(declared) > settings.MCP_MAX_MEDIA_BYTES:
                    raise MCPServiceError("limit_exceeded", "Remote media exceeds the configured limit.", path="source_url")
                chunks = []
                size = 0
                for chunk in response.iter_content(chunk_size=64 * 1024):
                    if not chunk:
                        continue
                    size += len(chunk)
                    if size > settings.MCP_MAX_MEDIA_BYTES:
                        raise MCPServiceError("limit_exceeded", "Remote media exceeds the configured limit.", path="source_url")
                    chunks.append(chunk)
                content = b"".join(chunks)
            finally:
                response.close()
        except MCPServiceError:
            raise
        except Exception as exc:
            raise MCPServiceError("media_fetch_failed", "Remote media could not be retrieved.", path="source_url", retryable=True) from exc
    if not content:
        raise MCPServiceError("validation_error", "Media content is empty.", path=sources[0])
    if len(content) > settings.MCP_MAX_MEDIA_BYTES:
        raise MCPServiceError("limit_exceeded", "Media exceeds the configured limit.", path=sources[0])
    return content


def _inspect_media(content):
    kind = None
    if content.startswith(b"\xff\xd8\xff"):
        kind = SUPPORTED_MEDIA[0]
    elif content.startswith(b"\x89PNG\r\n\x1a\n"):
        kind = SUPPORTED_MEDIA[1]
    elif len(content) >= 12 and content[:4] == b"RIFF" and content[8:12] == b"WEBP":
        kind = SUPPORTED_MEDIA[2]
    elif content.startswith((b"GIF87a", b"GIF89a")):
        kind = SUPPORTED_MEDIA[3]
    elif len(content) >= 16 and content[4:8] == b"ftyp":
        box_size = int.from_bytes(content[:4], "big")
        if 8 <= box_size <= len(content) and (b"moov" in content[box_size:] or b"mdat" in content[box_size:]):
            kind = SUPPORTED_MEDIA[4]
    if kind is None:
        raise MCPServiceError("unsupported_media", "Media signature is not a supported JPEG, PNG, WebP, GIF, or MP4.")
    if kind[0] != "mp4":
        try:
            with Image.open(io.BytesIO(content)) as image:
                width, height = image.size
                if width < 1 or height < 1 or width * height > settings.MCP_MAX_IMAGE_PIXELS:
                    raise MCPServiceError("limit_exceeded", "Image dimensions exceed the configured limit.")
                image.verify()
        except MCPServiceError:
            raise
        except (UnidentifiedImageError, OSError, ValueError) as exc:
            raise MCPServiceError("invalid_media", "Image content is corrupt or does not match its signature.") from exc
    return kind


def _generated_root():
    root = Path(settings.MCP_GENERATED_ROOT)
    if root.exists() and root.is_symlink():
        raise MCPServiceError("unsafe_path", "Generated storage root may not be a symlink.")
    root.mkdir(parents=True, exist_ok=True)
    resolved = root.resolve()
    data_root = Path(settings.DATA_STORE).resolve()
    try:
        resolved.relative_to(data_root)
    except ValueError as exc:
        raise MCPServiceError("unsafe_path", "Generated storage must be contained by DATA_STORE.") from exc
    return resolved, data_root


def _write_media_file(media, content, extension):
    root, data_root = _generated_root()
    folder = root / "media" / str(media.item.domain_id) / str(media.id)
    folder.mkdir(parents=True, exist_ok=True)
    if folder.is_symlink() or root not in folder.resolve().parents:
        raise MCPServiceError("unsafe_path", "Generated media directory is unsafe.")
    destination = folder / f"{secrets.token_hex(16)}.{extension}"
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(dir=folder, prefix=".upload-", delete=False) as handle:
            temporary = Path(handle.name)
            os.chmod(temporary, 0o600)
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, destination)
    except Exception:
        if temporary is not None:
            temporary.unlink(missing_ok=True)
        raise
    return str(destination.relative_to(data_root))


def controlled_media_path(media):
    if not media.original_media:
        raise MCPServiceError("not_found", "Media file is not available.")
    root, data_root = _generated_root()
    candidate = data_root / media.original_media
    if candidate.is_symlink():
        raise MCPServiceError("unsafe_path", "Media path is unsafe.")
    resolved = candidate.resolve(strict=True)
    if root not in resolved.parents or not resolved.is_file():
        raise MCPServiceError("unsafe_path", "Media path is outside controlled storage.")
    return resolved


def controlled_artifact_path(job):
    if not job.artifact_path or (job.artifact_expires_at and job.artifact_expires_at <= timezone.now()):
        raise MCPServiceError("not_found", "Artifact is not available.")
    root, _data_root = _generated_root()
    candidate = root / job.artifact_path
    if candidate.is_symlink():
        raise MCPServiceError("unsafe_path", "Artifact path is unsafe.")
    resolved = candidate.resolve(strict=True)
    if root not in resolved.parents or not resolved.is_file():
        raise MCPServiceError("unsafe_path", "Artifact path is outside controlled storage.")
    return resolved


def _delete_controlled_path(stored_path):
    if not stored_path:
        return None
    try:
        root, data_root = _generated_root()
        candidate = data_root / stored_path
        if candidate.is_symlink():
            return "symlink_rejected"
        resolved = candidate.resolve(strict=True)
        if root not in resolved.parents or not resolved.is_file():
            return "outside_controlled_storage"
        resolved.unlink()
        return None
    except FileNotFoundError:
        return None
    except (MCPServiceError, OSError, RuntimeError):
        return "cleanup_failed"


def create_media(client, payload):
    require_scope(client, "domain:owner")
    if not isinstance(payload, dict) or "item_id" not in payload:
        raise MCPServiceError("validation_error", "item_id is required.", path="item_id")
    unknown = sorted(set(payload) - {"item_id", "content_base64", "source_url"})
    if unknown:
        raise MCPServiceError("validation_error", f"Unknown fields: {', '.join(unknown)}.", path=unknown[0])
    item = _item_for_client(client, payload["item_id"])
    content = _read_media_source(payload)
    _name, _mime, extension, media_type = _inspect_media(content)
    media = RVMedia.objects.create(item=item, media_type=media_type)
    stored_path = ""
    completed = False
    try:
        stored_path = _write_media_file(media, content, extension)
        media.original_media = stored_path
        media.primary_media = stored_path
        media.save(update_fields=["original_media", "primary_media"])
        completed = True
    finally:
        if not completed:
            media.delete()
            _delete_controlled_path(stored_path)
    audit(client, "create_media", MCPAuditRecord.Outcome.SUCCESS, domain=item.domain, ids=[media.id])
    return media


def update_media(client, media_id, payload):
    require_scope(client, "domain:owner")
    if not isinstance(payload, dict):
        raise MCPServiceError("validation_error", "Media must be an object.", path="media")
    unknown = sorted(set(payload) - {"expected_revision", "content_base64", "source_url"})
    if unknown:
        raise MCPServiceError("validation_error", f"Unknown fields: {', '.join(unknown)}.", path=unknown[0])
    revision = _expected_revision(payload)
    content = _read_media_source(payload)
    _name, _mime, extension, media_type = _inspect_media(content)
    new_path = ""
    committed = False
    try:
        with transaction.atomic():
            media = _media_for_client(client, media_id, lock=True)
            if media.revision != revision:
                raise MCPServiceError(
                    "revision_conflict", "The media changed after the supplied revision.",
                    details={"current_revision": media.revision},
                )
            old_path = media.original_media
            new_path = _write_media_file(media, content, extension)
            media.media_type = media_type
            media.original_media = new_path
            media.primary_media = new_path
            media.thumbnail = ""
            media.save(update_fields=["media_type", "original_media", "primary_media", "thumbnail"])
            transaction.on_commit(lambda: _delete_controlled_path(old_path))
        committed = True
    finally:
        if new_path and not committed:
            _delete_controlled_path(new_path)
    audit(client, "update_media", MCPAuditRecord.Outcome.SUCCESS, domain=media.item.domain, ids=[media.id])
    return media


def create_service(client, payload):
    require_scope(client, "domain:owner")
    if not isinstance(payload, dict):
        raise MCPServiceError("validation_error", "Service must be an object.", path="service")
    allowed = {"domain_id", "name", "type", "live", "hide_unmoderated"}
    unknown = sorted(set(payload) - allowed)
    if unknown:
        raise MCPServiceError("validation_error", f"Unknown fields: {', '.join(unknown)}.", path=unknown[0])
    for field in ("domain_id", "name", "type"):
        if field not in payload:
            raise MCPServiceError("validation_error", "Field is required.", path=field)
    domain = require_domain(client, payload["domain_id"])
    name = _bounded_string(payload["name"], "name", 512, required=True)
    service_type = payload["type"]
    if service_type not in RVService.Type.values:
        raise MCPServiceError("validation_error", "type is not supported.", path="type")
    values = {"live": payload.get("live", True), "hide_unmoderated": payload.get("hide_unmoderated", False)}
    if not all(isinstance(value, bool) for value in values.values()):
        raise MCPServiceError("validation_error", "live and hide_unmoderated must be boolean.")
    service = RVService.objects.create(domain=domain, name=name, type=service_type, **values)
    audit(client, "create_service", MCPAuditRecord.Outcome.SUCCESS, domain=domain, ids=[service.id])
    return service


def update_service(client, service_id, payload):
    require_scope(client, "domain:owner")
    if not isinstance(payload, dict):
        raise MCPServiceError("validation_error", "Service must be an object.", path="service")
    unknown = sorted(set(payload) - {"expected_revision", "name", "live", "hide_unmoderated"})
    if unknown:
        raise MCPServiceError("validation_error", f"Unknown or immutable fields: {', '.join(unknown)}.", path=unknown[0])
    revision = _expected_revision(payload)
    values = {}
    if "name" in payload:
        values["name"] = _bounded_string(payload["name"], "name", 512, required=True)
    for field in ("live", "hide_unmoderated"):
        if field in payload:
            if not isinstance(payload[field], bool):
                raise MCPServiceError("validation_error", f"{field} must be boolean.", path=field)
            values[field] = payload[field]
    if not values:
        raise MCPServiceError("validation_error", "At least one writable field is required.", path="service")
    service = RVService.objects.select_related("domain").filter(
        pk=service_id, domain_id__in=accessible_domain_ids(client)
    ).first()
    if service is None:
        raise MCPServiceError("not_found", "Service not found.", path="service_id")
    updated = RVService.objects.filter(pk=service.pk, revision=revision).update(
        **values, revision=F("revision") + 1, updated_at=timezone.now()
    )
    if not updated:
        current = RVService.objects.only("revision").get(pk=service.pk)
        raise MCPServiceError(
            "revision_conflict", "The service changed after the supplied revision.",
            details={"current_revision": current.revision},
        )
    service.refresh_from_db()
    audit(client, "update_service", MCPAuditRecord.Outcome.SUCCESS, domain=service.domain, ids=[service.id])
    return service


def serialize_job(job):
    return {
        "id": job.id,
        "domain_id": job.domain_id,
        "operation": job.operation,
        "status": job.status,
        "progress": {"current": job.progress_current, "total": job.progress_total},
        "attempt_count": job.attempt_count,
        "max_attempts": job.max_attempts,
        "warnings": job.warnings,
        "failures": job.failures,
        "result": job.result,
        "created_at": job.created_at.isoformat(),
        "started_at": job.started_at.isoformat() if job.started_at else None,
        "finished_at": job.finished_at.isoformat() if job.finished_at else None,
        "artifact": (
            {
                "size": job.artifact_size,
                "sha256": job.artifact_sha256,
                "expires_at": job.artifact_expires_at.isoformat() if job.artifact_expires_at else None,
                "download_url": f"/mcp-download/jobs/{job.id}/",
            }
            if job.artifact_path
            else None
        ),
    }


def _job_for_client(client, job_id):
    try:
        job_id = int(job_id)
    except (TypeError, ValueError) as exc:
        raise MCPServiceError("validation_error", "job_id must be an integer.", path="job_id") from exc
    job = MCPJob.objects.filter(pk=job_id, domain_id__in=accessible_domain_ids(client)).first()
    if job is None:
        raise MCPServiceError("not_found", "Job not found.", path="job_id")
    return job


def get_job(client, job_id):
    require_scope(client, "domain:owner")
    return serialize_job(_job_for_client(client, job_id))


def list_jobs(client, *, domain_id=None, status=None, cursor=None, limit=None):
    require_scope(client, "domain:owner")
    limit = _page_limit(limit)
    binding = {"domain_id": domain_id, "status": status}
    queryset = MCPJob.objects.filter(domain_id__in=accessible_domain_ids(client))
    if domain_id is not None:
        require_domain(client, domain_id)
        queryset = queryset.filter(domain_id=domain_id)
    if status is not None:
        if status not in MCPJob.Status.values:
            raise MCPServiceError("validation_error", "status is invalid.", path="status")
        queryset = queryset.filter(status=status)
    queryset = queryset.filter(id__gt=_decode_id_cursor(cursor, binding)).order_by("id")
    page = list(queryset[: limit + 1])
    has_more = len(page) > limit
    page = page[:limit]
    return {
        "ok": True,
        "jobs": [serialize_job(job) for job in page],
        "next_cursor": _encode_id_cursor(page[-1].id, binding) if has_more else None,
    }


def submit_processing_job(client, domain_id, operation, selector=None):
    require_scope(client, "domain:owner")
    domain = require_domain(client, domain_id)
    if operation not in {"domain_metadata_refresh", "media_mirror", "link_enrichment"}:
        raise MCPServiceError("validation_error", "Processing operation is not supported.", path="operation")
    selector = selector or {}
    if not isinstance(selector, dict):
        raise MCPServiceError("validation_error", "selector must be an object.", path="selector")
    allowed_key = {"domain_metadata_refresh": None, "media_mirror": "item_ids", "link_enrichment": "link_ids"}[operation]
    if allowed_key is None:
        if selector:
            raise MCPServiceError("validation_error", "This operation does not accept a selector.", path="selector")
    else:
        if set(selector) != {allowed_key} or not isinstance(selector[allowed_key], list) or not selector[allowed_key]:
            raise MCPServiceError("validation_error", f"selector.{allowed_key} must be a non-empty list.", path=f"selector.{allowed_key}")
        if len(selector[allowed_key]) > settings.MCP_MAX_DESTRUCTIVE_RECORDS:
            raise MCPServiceError("limit_exceeded", "Selector exceeds the configured record limit.", path=f"selector.{allowed_key}")
        try:
            ids = sorted({int(value) for value in selector[allowed_key]})
        except (TypeError, ValueError) as exc:
            raise MCPServiceError("validation_error", "Selector IDs must be integers.", path=f"selector.{allowed_key}") from exc
        model = RVItem if allowed_key == "item_ids" else RVLink
        domain_lookup = "domain_id" if model is RVItem else "item__domain_id"
        found = set(model.objects.filter(id__in=ids, **{domain_lookup: domain.id}).values_list("id", flat=True))
        if found != set(ids):
            raise MCPServiceError("not_found", "One or more selected records were not found.", path=f"selector.{allowed_key}")
        selector = {allowed_key: ids}
    try:
        job = enqueue_job(client, domain, operation, selector)
    except (TypeError, ValueError) as exc:
        raise MCPServiceError("validation_error", str(exc), path="operation") from exc
    audit(client, "submit_processing_job", MCPAuditRecord.Outcome.SUCCESS, domain=domain, ids=[job.id], details={"operation": operation})
    return job


def safe_service_with_count(service):
    return serialize_service(service, RVItem.objects.filter(service=service).count())


def enrich_link(link):
    if not validate_public_http_url(link.url):
        raise MCPServiceError("unsafe_url", "Link URL is not a public HTTP(S) address.")
    try:
        response = get_public_url(
            link.url,
            timeout=30,
            headers={"User-Agent": settings.FEEDS_USER_AGENT},
            stream=True,
        )
        try:
            response.raise_for_status()
            content_type = response.headers.get("Content-Type", "").split(";", 1)[0].lower()
            if content_type and content_type not in {"text/html", "application/xhtml+xml"}:
                raise MCPServiceError("unsupported_content", "Link enrichment requires an HTML response.")
            chunks = []
            size = 0
            for chunk in response.iter_content(chunk_size=32 * 1024):
                if not chunk:
                    continue
                size += len(chunk)
                if size > settings.MCP_MAX_LINK_RESPONSE_BYTES:
                    raise MCPServiceError("limit_exceeded", "Link response exceeds the configured limit.")
                chunks.append(chunk)
            soup = BeautifulSoup(b"".join(chunks), "html5lib")
        finally:
            response.close()
    except MCPServiceError:
        raise
    except Exception as exc:
        raise MCPServiceError("link_fetch_failed", "Link metadata could not be retrieved.", retryable=True) from exc

    title = ""
    if soup.title and soup.title.string:
        title = soup.title.string.strip()
    description = ""
    description_tag = soup.find("meta", attrs={"name": lambda value: value and value.lower() == "description"})
    if description_tag:
        description = (description_tag.get("content") or "").strip()
    image_url = ""
    image_tag = soup.find("meta", attrs={"property": "og:image"})
    if image_tag:
        candidate = (image_tag.get("content") or "").strip()
        if candidate and validate_public_http_url(candidate):
            image_url = candidate
    link.title = title[:512]
    link.description = description[:10_000]
    link.image = image_url[:512]
    link.save(update_fields=["title", "description", "image"])
    return link
