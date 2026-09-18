"""
fde_component.jobs — SP-05 job orchestration layer.

Subpackages:
  base.py        CheckpointedJob base class + retry/DLQ decorator
  idempotency.py Idempotency key builder + dedup check
  scheduler.py   Incremental sync schedule enqueuer
  exceptions.py  Job-specific exception types
"""

from .base import CheckpointedJob, retry_dlq
from .exceptions import (
    JobInterrupted,
    JobFailed,
    JobDeadLettered,
    DedupDuplicate,
)
from .idempotency import IdempotencyKey, check_dedup, mark_dedup
from .scheduler import enqueue_due_schedules

__all__ = [
    "CheckpointedJob",
    "retry_dlq",
    "JobInterrupted",
    "JobFailed",
    "JobDeadLeted",
    "DedupDuplicate",
    "IdempotencyKey",
    "check_dedup",
    "mark_dedup",
    "enqueue_due_schedules",
]
