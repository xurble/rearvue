from __future__ import annotations

import logging
from dataclasses import dataclass

import flickrapi
import requests
from django.core.exceptions import ObjectDoesNotExist
from django.db import DatabaseError, transaction
from PIL import Image

OPERATIONAL_EXCEPTIONS = (
    DatabaseError,
    ObjectDoesNotExist,
    flickrapi.exceptions.FlickrError,
    flickrapi.exceptions.CancelUpload,
    flickrapi.exceptions.LockingError,
    Image.DecompressionBombError,
    requests.RequestException,
    OSError,
    LookupError,
    TypeError,
    ValueError,
    RuntimeError,
)


def snapshot_media_ids(item) -> list[int]:
    """Record media rows that must survive until replacement fully succeeds."""

    return list(item.rvmedia_set.values_list("id", flat=True))


def complete_media_replacement(item, previous_media_ids: list[int]) -> None:
    """Atomically publish staged media and advance the item's mirror state."""

    with transaction.atomic():
        if previous_media_ids:
            item.rvmedia_set.filter(id__in=previous_media_ids).delete()
        item.mirror_state = 1
        item.save(update_fields=["mirror_state"])


def fail_media_replacement(item, previous_media_ids: list[int]) -> None:
    """Discard staged rows, preserve prior media, and make the item retryable."""

    staged_media = item.rvmedia_set.exclude(id__in=previous_media_ids)
    staged_media.delete()
    if item.mirror_state != 0:
        item.mirror_state = 0
        item.save(update_fields=["mirror_state"])


@dataclass(frozen=True)
class OperationResult:
    """Scheduler-visible outcome for a batch that may safely continue after failures."""

    processed: int = 0
    failed: int = 0

    def __add__(self, other: OperationResult) -> OperationResult:
        if not isinstance(other, OperationResult):
            return NotImplemented
        return OperationResult(
            processed=self.processed + other.processed,
            failed=self.failed + other.failed,
        )


def log_safe_exception(
    logger: logging.Logger,
    message: str,
    *args: object,
    exc: BaseException,
    level: int = logging.ERROR,
) -> None:
    """Log useful exception metadata without rendering secret-bearing values."""

    details = [f"error_type={type(exc).__name__}"]
    status_code = getattr(getattr(exc, "response", None), "status_code", None)
    if isinstance(status_code, int):
        details.append(f"status_code={status_code}")
    errno = getattr(exc, "errno", None)
    if isinstance(errno, int):
        details.append(f"errno={errno}")
    lineno = getattr(exc, "lineno", None)
    colno = getattr(exc, "colno", None)
    if isinstance(lineno, int):
        details.append(f"line={lineno}")
    if isinstance(colno, int):
        details.append(f"column={colno}")

    logger.log(level, f"{message} {' '.join(details)}", *args)
