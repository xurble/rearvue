import base64
import binascii
import json
from datetime import datetime
from urllib.parse import quote

from django.conf import settings
from django.db import DataError, IntegrityError, transaction

from rvsite.models import RVItem, RVService

from .jobs import enqueue_job
from .models import MCPAuditRecord
from .services import (
    MCPServiceError,
    accessible_domain_ids,
    audit,
    normalize_raw_data,
    require_domain,
    require_scope,
    sanitize_caption,
)

TWITTER_PREFIX = "window.YTD.tweets.part0 = "
ARCHIVE_REQUEST_ENVELOPE_RESERVE_BYTES = 16 * 1024


def maximum_archive_bytes():
    request_capacity = max(
        0,
        settings.MCP_MAX_REQUEST_BODY_BYTES - ARCHIVE_REQUEST_ENVELOPE_RESERVE_BYTES,
    )
    base64_capacity = (request_capacity // 4) * 3
    return min(settings.MCP_MAX_ARCHIVE_BYTES, base64_capacity)


def decode_twitter_archive(source):
    if isinstance(source, bytes):
        raw = source
    elif isinstance(source, str):
        raw = source.encode("utf-8")
    else:
        raise MCPServiceError("validation_error", "Archive must be text or bytes.", path="archive")
    if len(raw) > settings.MCP_MAX_ARCHIVE_BYTES:
        raise MCPServiceError("limit_exceeded", "Archive exceeds the configured byte limit.", path="archive")
    try:
        text = raw.decode("utf-8-sig").strip()
    except UnicodeDecodeError as exc:
        raise MCPServiceError("validation_error", "Archive must be UTF-8.", path="archive") from exc
    text = text.removeprefix(TWITTER_PREFIX)
    text = text.removesuffix(";")
    try:
        records = json.loads(text)
    except json.JSONDecodeError as exc:
        raise MCPServiceError("validation_error", "Archive JSON is invalid.", path="archive") from exc
    if not isinstance(records, list):
        raise MCPServiceError("validation_error", "Twitter archive must contain a list.", path="archive")
    if len(records) > settings.MCP_MAX_ARCHIVE_RECORDS:
        raise MCPServiceError("limit_exceeded", "Archive contains too many records.", path="archive")
    return records


def _tweet_values(service, wrapper):
    if not isinstance(wrapper, dict) or not isinstance(wrapper.get("tweet"), dict):
        raise MCPServiceError("validation_error", "Record must contain a tweet object.")
    tweet = wrapper["tweet"]
    tweet_id = tweet.get("id") or tweet.get("id_str")
    text = tweet.get("full_text")
    created_at = tweet.get("created_at")
    if not isinstance(tweet_id, str) or not tweet_id or len(tweet_id) > 128:
        raise MCPServiceError("validation_error", "Tweet id is invalid.")
    if not isinstance(text, str):
        raise MCPServiceError("validation_error", "Tweet full_text is invalid.")
    if text.startswith(("RT @", "@")):
        return None
    if not isinstance(created_at, str):
        raise MCPServiceError("validation_error", "Tweet created_at is invalid.")
    try:
        created = datetime.strptime(created_at, "%a %b %d %H:%M:%S %z %Y")
    except ValueError as exc:
        raise MCPServiceError("validation_error", "Tweet created_at is invalid.") from exc
    entities = tweet.get("entities", {})
    extended_entities = tweet.get("extended_entities", {})
    if not isinstance(entities, dict) or not isinstance(extended_entities, dict):
        raise MCPServiceError("validation_error", "Tweet entities are invalid.")
    username = quote(str(service.config.get("username", ""))[:128], safe="")
    return tweet_id, {
        "domain": service.domain,
        "date_created": created.date(),
        "datetime_created": created,
        "remote_url": f"https://twitter.com/{username}/status/{quote(tweet_id, safe='')}",
        "caption": sanitize_caption(text, "plain"),
        "raw_data": normalize_raw_data(tweet),
        "mirror_state": 0 if (extended_entities or entities.get("media")) else 1,
    }


def import_twitter_archive(service, source, report=None):
    if service.type != RVService.Type.TWITTER:
        raise MCPServiceError("validation_error", "Service must be a Twitter archive service.", path="service_id")
    records = decode_twitter_archive(source)
    report = report or (lambda **_progress: None)
    report(current=0, total=len(records))
    processed = 0
    skipped = 0
    failures = []
    for index, wrapper in enumerate(records):
        try:
            parsed = _tweet_values(service, wrapper)
            if parsed is None:
                skipped += 1
            else:
                tweet_id, values = parsed
                with transaction.atomic():
                    item, created = RVItem.objects.get_or_create(
                        service=service,
                        item_id=tweet_id,
                        defaults=values,
                    )
                    if not created:
                        changed = False
                        for field, value in values.items():
                            if getattr(item, field) != value:
                                setattr(item, field, value)
                                changed = True
                        if changed:
                            item.save(update_fields=list(values))
                processed += 1
        except MCPServiceError as exc:
            failures.append({"index": index, "code": exc.code, "message": exc.message})
        except (DataError, IntegrityError):
            failures.append({"index": index, "code": "record_failed", "message": "Record could not be imported."})
        report(current=index + 1, total=len(records))
    return {
        "submitted_count": len(records),
        "processed_count": processed,
        "skipped_count": skipped,
        "failed_count": len(failures),
        "failures": failures,
    }


def submit_twitter_archive(client, domain_id, service_id, archive_base64):
    require_scope(client, "domain:owner")
    domain = require_domain(client, domain_id)
    service = RVService.objects.filter(
        pk=service_id,
        domain=domain,
        domain_id__in=accessible_domain_ids(client),
        type=RVService.Type.TWITTER,
    ).first()
    if service is None:
        raise MCPServiceError("not_found", "Twitter service not found.", path="service_id")
    if not isinstance(archive_base64, str):
        raise MCPServiceError("validation_error", "archive_base64 must be a string.", path="archive_base64")
    effective_maximum = maximum_archive_bytes()
    if len(archive_base64) > ((effective_maximum + 2) // 3) * 4:
        raise MCPServiceError(
            "limit_exceeded",
            "Encoded archive exceeds the effective request limit.",
            path="archive_base64",
        )
    try:
        archive = base64.b64decode(archive_base64, validate=True)
    except (ValueError, binascii.Error) as exc:
        raise MCPServiceError("validation_error", "archive_base64 is invalid.", path="archive_base64") from exc
    if len(archive) > effective_maximum:
        raise MCPServiceError("limit_exceeded", "Archive exceeds the effective request limit.", path="archive_base64")
    decode_twitter_archive(archive)
    job = enqueue_job(
        client,
        domain,
        "twitter_archive_import",
        {"service_id": service.id, "archive_base64": archive_base64},
    )
    audit(client, "submit_twitter_archive", MCPAuditRecord.Outcome.SUCCESS, domain=domain, ids=[job.id])
    return job


def run_twitter_archive_job(job, report):
    service = RVService.objects.filter(
        pk=job.payload.get("service_id"), domain_id=job.domain_id, type=RVService.Type.TWITTER
    ).first()
    if service is None:
        raise MCPServiceError("not_found", "Twitter service no longer exists.")
    try:
        archive = base64.b64decode(job.payload.get("archive_base64", ""), validate=True)
    except (ValueError, binascii.Error) as exc:
        raise MCPServiceError("validation_error", "Stored archive is invalid.") from exc
    return import_twitter_archive(service, archive, report=report)
