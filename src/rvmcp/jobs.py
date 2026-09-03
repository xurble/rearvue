import json
import logging
import secrets
import threading
from collections.abc import Callable
from dataclasses import dataclass
from datetime import timedelta
from typing import Any

from django.conf import settings
from django.db import close_old_connections, transaction
from django.db.models import F, Value
from django.db.models.functions import Coalesce
from django.utils import timezone

from .models import MCPClient, MCPJob

logger = logging.getLogger(__name__)
JobHandler = Callable[[MCPJob, Callable[..., None]], dict[str, Any] | None]
JOB_REGISTRY: dict[str, JobHandler] = {}


@dataclass
class JobExecutionError(Exception):
    message: str
    retryable: bool = True
    code: str = "job_failed"


def register_job(operation: str):
    if not operation or len(operation) > 64:
        raise ValueError("Job operation names must be 1–64 characters.")

    def decorate(handler: JobHandler):
        if operation in JOB_REGISTRY:
            raise RuntimeError(f"Duplicate MCP job operation: {operation}")
        JOB_REGISTRY[operation] = handler
        return handler

    return decorate


def registered_operations():
    return tuple(sorted(JOB_REGISTRY))


def enqueue_job(client, domain, operation, payload=None):
    if operation not in JOB_REGISTRY:
        raise ValueError("Unsupported MCP job operation.")
    if not client.domains.filter(pk=domain.pk).exists():
        raise ValueError("Domain is not granted to this client.")
    payload = payload or {}
    if not isinstance(payload, dict):
        raise TypeError("Job payload must be an object.")
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    if len(encoded.encode("utf-8")) > settings.MCP_MAX_REQUEST_BODY_BYTES:
        raise ValueError("Job payload exceeds the configured request limit.")
    return MCPJob.objects.create(
        client=client,
        domain=domain,
        operation=operation,
        payload=payload,
        max_attempts=min(100, max(1, settings.MCP_MAX_JOB_ATTEMPTS)),
    )


def recover_expired_jobs(now=None):
    now = now or timezone.now()
    retryable = MCPJob.objects.filter(
        status=MCPJob.Status.RUNNING,
        leased_until__lt=now,
        attempt_count__lt=F("max_attempts"),
    )
    retry_count = retryable.update(
        status=MCPJob.Status.QUEUED,
        run_after=now,
        lease_owner="",
        lease_token="",
        leased_until=None,
        heartbeat_at=None,
    )
    failed_count = MCPJob.objects.filter(
        status=MCPJob.Status.RUNNING,
        leased_until__lt=now,
        attempt_count__gte=F("max_attempts"),
    ).update(
        status=MCPJob.Status.FAILED,
        result={"ok": False, "error": {"code": "lease_expired", "message": "Worker lease expired."}},
        lease_owner="",
        lease_token="",
        leased_until=None,
        finished_at=now,
    )
    return retry_count, failed_count


def claim_next_job(worker_id, now=None):
    if not isinstance(worker_id, str) or not worker_id or len(worker_id) > 128:
        raise ValueError("worker_id must be 1–128 characters.")
    now = now or timezone.now()
    recover_expired_jobs(now)
    lease_seconds = max(5, settings.MCP_JOB_LEASE_SECONDS)
    lease_until = now + timedelta(seconds=lease_seconds)

    candidate_ids = list(
        MCPJob.objects.filter(status=MCPJob.Status.QUEUED, run_after__lte=now)
        .order_by("run_after", "id")
        .values_list("id", flat=True)[:20]
    )
    for job_id in candidate_ids:
        lease_token = secrets.token_hex(32)
        claimed = MCPJob.objects.filter(
            pk=job_id,
            status=MCPJob.Status.QUEUED,
            run_after__lte=now,
        ).update(
            status=MCPJob.Status.RUNNING,
            attempt_count=F("attempt_count") + 1,
            lease_owner=worker_id,
            lease_token=lease_token,
            leased_until=lease_until,
            heartbeat_at=now,
            started_at=Coalesce("started_at", Value(now)),
        )
        if claimed:
            return MCPJob.objects.select_related("client", "domain").get(pk=job_id)
    return None


def _require_job_authorized(job):
    client = MCPClient.objects.filter(pk=job.client_id).first()
    if (
        client is None
        or not client.is_active
        or "domain:owner" not in client.scopes
        or not client.domains.filter(pk=job.domain_id).exists()
    ):
        raise JobExecutionError(
            "Job authorization was revoked before execution.",
            retryable=False,
            code="authorization_revoked",
        )
    job.client = client


def heartbeat(job, *, current=None, total=None):
    _require_job_authorized(job)
    now = timezone.now()
    updates = {
        "heartbeat_at": now,
        "leased_until": now + timedelta(seconds=max(5, settings.MCP_JOB_LEASE_SECONDS)),
    }
    if current is not None:
        if not isinstance(current, int) or current < 0:
            raise ValueError("Progress must be a non-negative integer.")
        updates["progress_current"] = current
    if total is not None:
        if not isinstance(total, int) or total < 0:
            raise ValueError("Progress must be a non-negative integer.")
        updates["progress_total"] = total
    updated = MCPJob.objects.filter(
        pk=job.pk,
        status=MCPJob.Status.RUNNING,
        lease_owner=job.lease_owner,
        lease_token=job.lease_token,
    ).update(**updates)
    if not updated:
        raise JobExecutionError("Job lease was lost.", retryable=True, code="lease_lost")


def _lease_renewal_interval():
    return max(0.1, max(5, settings.MCP_JOB_LEASE_SECONDS) / 3)


class _LeaseRenewer:
    def __init__(self, job):
        self.job = job
        self._stop = threading.Event()
        self._error = None
        self._thread = threading.Thread(
            target=self._run,
            name=f"mcp-job-lease-{job.pk}",
            daemon=True,
        )

    def start(self):
        self._thread.start()

    def stop(self):
        self._stop.set()
        self._thread.join(timeout=min(5, max(1, _lease_renewal_interval() * 2)))
        if self._thread.is_alive():
            self._error = JobExecutionError(
                "Job lease renewal did not stop cleanly.",
                retryable=True,
                code="lease_renewal_failed",
            )

    def raise_if_failed(self):
        if self._error is not None:
            raise self._error

    def _run(self):
        close_old_connections()
        try:
            while not self._stop.wait(_lease_renewal_interval()):
                try:
                    heartbeat(self.job)
                except JobExecutionError as exc:
                    self._error = exc
                    return
                except Exception:
                    logger.exception(
                        "MCP job lease renewal failed",
                        extra={"job_id": self.job.pk, "operation": self.job.operation},
                    )
                    self._error = JobExecutionError(
                        "Job lease renewal failed.",
                        retryable=True,
                        code="lease_renewal_failed",
                    )
                    return
        finally:
            close_old_connections()


def _bounded_result(result):
    result = result or {}
    if not isinstance(result, dict):
        raise JobExecutionError("Job handlers must return an object.", retryable=False, code="invalid_result")
    encoded = json.dumps(result, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    if len(encoded.encode("utf-8")) > settings.MCP_JOB_RESULT_MAX_BYTES:
        raise JobExecutionError("Job result exceeds the configured limit.", retryable=False, code="result_too_large")
    return result


def _finish_success(job, result):
    now = timezone.now()
    updated = MCPJob.objects.filter(
        pk=job.pk,
        status=MCPJob.Status.RUNNING,
        lease_owner=job.lease_owner,
        lease_token=job.lease_token,
    ).update(
        status=MCPJob.Status.SUCCEEDED,
        result={**_bounded_result(result), "ok": True},
        progress_current=F("progress_total"),
        lease_owner="",
        lease_token="",
        leased_until=None,
        heartbeat_at=now,
        finished_at=now,
    )
    if not updated:
        raise JobExecutionError("Job lease was lost before completion.", code="lease_lost")


def _finish_failure(job, error):
    now = timezone.now()
    with transaction.atomic():
        current = MCPJob.objects.select_for_update().filter(
            pk=job.pk,
            status=MCPJob.Status.RUNNING,
            lease_owner=job.lease_owner,
            lease_token=job.lease_token,
        ).first()
        if current is None:
            return
        failure = {"attempt": current.attempt_count, "code": error.code, "message": error.message}
        current.failures = [*current.failures, failure]
        current.lease_owner = ""
        current.lease_token = ""
        current.leased_until = None
        current.heartbeat_at = now
        if error.retryable and current.attempt_count < current.max_attempts:
            delay = settings.MCP_JOB_RETRY_BASE_SECONDS * (2 ** (current.attempt_count - 1))
            current.status = MCPJob.Status.QUEUED
            current.run_after = now + timedelta(seconds=delay)
            update_fields = [
                "failures", "lease_owner", "lease_token", "leased_until", "heartbeat_at",
                "status", "run_after", "updated_at",
            ]
        else:
            current.status = MCPJob.Status.FAILED
            current.result = {"ok": False, "error": {"code": error.code, "message": error.message}}
            current.finished_at = now
            update_fields = [
                "failures", "lease_owner", "lease_token", "leased_until", "heartbeat_at",
                "status", "result", "finished_at", "updated_at",
            ]
        current.save(update_fields=update_fields)


def execute_claimed_job(job):
    handler = JOB_REGISTRY.get(job.operation)
    if handler is None:
        _finish_failure(
            job,
            JobExecutionError("The stored operation is not registered.", retryable=False, code="unknown_operation"),
        )
        return
    try:
        _require_job_authorized(job)
        renewer = _LeaseRenewer(job)
        renewer.start()
        try:
            result = handler(job, lambda **progress: heartbeat(job, **progress))
        finally:
            renewer.stop()
        renewer.raise_if_failed()
        _finish_success(job, result)
    except JobExecutionError as exc:
        _finish_failure(job, exc)
    except Exception:
        logger.exception("Unhandled MCP job failure", extra={"job_id": job.pk, "operation": job.operation})
        _finish_failure(job, JobExecutionError("The job failed unexpectedly."))


def run_one_job(worker_id):
    job = claim_next_job(worker_id)
    if job is None:
        return False
    execute_claimed_job(job)
    return True


@register_job("domain_metadata_refresh")
def domain_metadata_refresh(job, report):
    from .services import refresh_domain_metadata

    report(current=0, total=1)
    refresh_domain_metadata(job.domain_id)
    report(current=1, total=1)
    return {"domain_id": job.domain_id, "refreshed": True}


@register_job("media_mirror")
def media_mirror(job, report):
    from rvservices import (
        flickr_service,
        instagram_graph_service,
        rss_service,
        twitter_service,
    )
    from rvsite.models import RVItem

    item_ids = job.payload.get("item_ids", [])
    items = list(
        RVItem.objects.select_related("service")
        .filter(id__in=item_ids, domain_id=job.domain_id)
        .order_by("id")
    )
    if len(items) != len(set(item_ids)):
        raise JobExecutionError("One or more selected items no longer exist.", retryable=False, code="not_found")
    report(current=0, total=len(items))
    failures = []
    processed = 0
    for index, item in enumerate(items, start=1):
        try:
            if item.service.type == "twitter":
                result = twitter_service.mirror_twitter(specific_item=item)
            elif item.service.type == "rss":
                result = rss_service.mirror_rss(specific_item=item)
            elif item.service.type == "instagram":
                result = instagram_graph_service.mirror_instagram(specific_item=item)
            elif item.service.type == "flickr":
                client = flickr_service._flickr_client(item.service)
                previous_ids = set(item.rvmedia_set.values_list("id", flat=True))
                flickr_service._mirror_flickr_item(client, item)
                from rvservices.results import complete_media_replacement

                complete_media_replacement(item, previous_ids)
                result = None
            else:
                raise ValueError("Unsupported service type")
            if result is not None and result.failed:
                raise ValueError("Media mirroring failed")
            processed += 1
        except Exception:
            logger.exception("MCP media mirror failed", extra={"job_id": job.id, "item_id": item.id})
            failures.append({"item_id": item.id, "code": "mirror_failed"})
        report(current=index, total=len(items))
    job.failures = failures
    job.save(update_fields=["failures", "updated_at"])
    return {"processed": processed, "failed": len(failures)}


@register_job("link_enrichment")
def link_enrichment(job, report):
    from rvsite.models import RVLink

    from .capabilities import MCPServiceError, enrich_link

    link_ids = job.payload.get("link_ids", [])
    links = list(
        RVLink.objects.select_related("item")
        .filter(id__in=link_ids, item__domain_id=job.domain_id)
        .order_by("id")
    )
    if len(links) != len(set(link_ids)):
        raise JobExecutionError("One or more selected links no longer exist.", retryable=False, code="not_found")
    report(current=0, total=len(links))
    failures = []
    processed = 0
    for index, link in enumerate(links, start=1):
        try:
            enrich_link(link)
            processed += 1
        except MCPServiceError as exc:
            failures.append({"link_id": link.id, "code": exc.code})
        report(current=index, total=len(links))
    job.failures = failures
    job.save(update_fields=["failures", "updated_at"])
    return {"processed": processed, "failed": len(failures)}


@register_job("export_ndjson")
def export_ndjson(job, report):
    from .exports import build_export_artifact
    from .services import MCPServiceError

    try:
        return build_export_artifact(job, report)
    except MCPServiceError as exc:
        raise JobExecutionError(exc.message, retryable=exc.retryable, code=exc.code) from exc


@register_job("twitter_archive_import")
def twitter_archive_import(job, report):
    from .archive_import import run_twitter_archive_job
    from .services import MCPServiceError

    try:
        result = run_twitter_archive_job(job, report)
    except MCPServiceError as exc:
        raise JobExecutionError(exc.message, retryable=exc.retryable, code=exc.code) from exc
    job.failures = result.pop("failures")
    job.save(update_fields=["failures", "updated_at"])
    return result
