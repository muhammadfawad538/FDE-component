"""
fde_component.jobs.exceptions
================================
Job-specific exception types used internally by CheckpointedJob and the
retry/DLQ decorator.  Application code raises these — the retry_dlq
decorator translates them into checkpoint saves, retries, or DLQ writes.

Hierarchy
---------
JobError
├── JobInterrupted   — worker was stopped (SIGTERM/SIGINT); job is resumable
├── JobFailed        — permanent failure after all retries exhausted
└── JobDeadLettered  — item moved to DLQ; raised after DLQ write so caller
                       can bail out of the record loop cleanly
└── DedupDuplicate   — record already processed; skip it, count it, move on
"""


class JobError(Exception):
    """Base for all SP-05 job exceptions."""


class JobInterrupted(JobError):
    """
    Raised by the worker's signal handler to stop processing mid-batch.
    CheckpointedJob.run() catches this, checkpoints current progress,
    marks the job Interrupted and exits cleanly.
    """


class JobFailed(JobError):
    """
    Raised when a record cannot be processed after all retries.
    The retry_dlq decorator converts this into a DLQ write then re-raises
    JobDeadLettered so the record loop can skip the item.
    """


class JobDeadLettered(JobError):
    """
    Raised after the retry_dlq decorator has written the item to SyncJobDLQ.
    The record loop catches this and continues to the next item —
    no further processing of this record.
    """


class DedupDuplicate(JobError):
    """
    Raised inside the record loop when check_dedup() finds an existing entry.
    Caught inside the loop — counted as a skip, not a failure.
    """
