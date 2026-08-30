from __future__ import annotations

import logging
from dataclasses import dataclass

import requests
from django.db import DatabaseError

OPERATIONAL_EXCEPTIONS = (
    DatabaseError,
    requests.RequestException,
    OSError,
    LookupError,
    TypeError,
    ValueError,
    RuntimeError,
)


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
