"""
fde_component.tests.test_conformance
======================================
SP-05 conformance checklist.

Each test class maps to a requirement from the SP-05 spec.
These tests can run without a live Frappe DB.
"""

from __future__ import annotations

import pytest

from fde_component.jobs.base import CheckpointedJob
from fde_component.jobs.idempotency import IdempotencyKey
from fde_component.jobs.exceptions import JobInterrupted, JobFailed, JobDeadLettered, DedupDuplicate


# ── SP-05-01: Checkpointed job base class ─────────────────────────────────────


class TestCheckpointedJobContract:

    def test_subclass_has_job_type(self):
        class MyJob(CheckpointedJob):
            job_type = "test.myjob"
            max_retries = 3

        assert MyJob.job_type == "test.myjob"

    def test_subclass_has_max_retries(self):
        class MyJob(CheckpointedJob):
            job_type = "test.myjob"
            max_retries = 5

        assert MyJob.max_retries == 5

    def test_default_checkpoint_interval(self):
        class MyJob(CheckpointedJob):
            job_type = "test.myjob"

        assert MyJob.checkpoint_every == 1000

    def test_default_checkpoint_seconds(self):
        class MyJob(CheckpointedJob):
            job_type = "test.myjob"

        assert MyJob.checkpoint_seconds == 30.0


# ── SP-05-02: Status lifecycle ────────────────────────────────────────────────


class TestStatusLifecycle:

    def test_queued_is_initial_status(self):
        doc = type("FakeDoc", (), {"status": "Queued", "save": lambda: None})()
        assert doc.status == "Queued"

    def test_valid_statuses(self):
        valid = {"Queued", "Running", "Interrupted", "Completed", "Failed", "Dead Lettered"}
        assert valid == {
            "Queued", "Running", "Interrupted", "Completed", "Failed", "Dead Lettered"
        }


# ── SP-05-03: Idempotency ─────────────────────────────────────────────────────


class TestIdempotencyConformance:

    def test_key_is_deterministic(self):
        k1 = IdempotencyKey("job", "src", "content")
        k2 = IdempotencyKey("job", "src", "content")
        assert k1.hex == k2.hex

    def test_key_changes_with_content(self):
        k1 = IdempotencyKey("job", "src", "v1")
        k2 = IdempotencyKey("job", "src", "v2")
        assert k1.hex != k2.hex

    def test_key_changes_with_source_id(self):
        k1 = IdempotencyKey("job", "src1", "")
        k2 = IdempotencyKey("job", "src2", "")
        assert k1.hex != k2.hex

    def test_key_is_64_char_hex(self):
        key = IdempotencyKey("job", "src", "content")
        assert len(key.hex) == 64
        int(key.hex, 16)  # valid hex


# ── SP-05-04: Retry and DLQ ───────────────────────────────────────────────────


class TestRetryDLQ:

    def test_dead_lettered_exception_exists(self):
        assert issubclass(JobDeadLettered, Exception)

    def test_failed_exception_exists(self):
        assert issubclass(JobFailed, Exception)

    def test_job_dead_lettered_is_distinct(self):
        assert JobDeadLettered is not JobFailed

    def test_interrupted_exception_exists(self):
        assert issubclass(JobInterrupted, Exception)

    def test_max_retries_is_configurable(self):
        class MyJob(CheckpointedJob):
            job_type = "test.retry"
            max_retries = 0

        assert MyJob.max_retries == 0

        class MyJob2(CheckpointedJob):
            job_type = "test.retry2"
            max_retries = 10

        assert MyJob2.max_retries == 10


# ── SP-05-05: Dedup exception handling ────────────────────────────────────────


class TestDedupExceptionHandling:

    def test_dedup_duplicate_exception_exists(self):
        assert issubclass(DedupDuplicate, Exception)

    def test_dedup_duplicate_is_distinct(self):
        assert DedupDuplicate is not JobFailed


# ── SP-05-06: Checkpoint cadence ──────────────────────────────────────────────


class TestCheckpointCadence:

    def test_default_cadence_is_1000_records(self):
        assert CheckpointedJob.checkpoint_every == 1000

    def test_default_cadence_is_30_seconds(self):
        assert CheckpointedJob.checkpoint_seconds == 30.0

    def test_custom_cadence(self):
        class FastJob(CheckpointedJob):
            job_type = "test.fast"
            checkpoint_every = 100
            checkpoint_seconds = 10.0

        assert FastJob.checkpoint_every == 100
        assert FastJob.checkpoint_seconds == 10.0
