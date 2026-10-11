"""
fde_component.tests.test_hardening
=====================================
Tests for SP-05 hardening fixes.

Covers three previously untested behaviors:
a) _write_dlq failure propagation — the record's dedup row must
   NOT be marked completed when the DLQ write fails.
b) _publish_progress throttling — progress publishes at most once
   per throttle interval, not once per record.
c) retry_dlq negative max_retries — ValueError before any record
   is processed.
"""

from __future__ import annotations

import time
import unittest
from unittest.mock import patch

import frappe

from fde_component.jobs.base import CheckpointedJob, retry_dlq
from fde_component.jobs.exceptions import JobDeadLettered, JobFailed


# ── Test job used by all three groups ─────────────────────────────────


class _StubJob(CheckpointedJob):
    """Minimal job: yields N records, none of which fail."""

    job_type = "test.hardening"
    max_retries = 1
    checkpoint_every = 1000
    checkpoint_seconds = 999.0

    def __init__(self, job_name: str, total: int = 3, **kwargs):
        super().__init__(job_name, **kwargs)
        self._total = total

    def iter_records(self, offset: int, redrive_key: str | None = None):
        for i in range(offset, self._total):
            yield {"id": f"record-{i}", "index": i}

    def process_record(self, record):
        pass


def _make_sync_job_doc(job_type: str = "test.hardening") -> str:
    doc = frappe.get_doc({
        "doctype": "SyncJob",
        "job_type": job_type,
        "status": "Queued",
        "total_records": 10,
        "processed": 0,
    })
    doc.insert(ignore_permissions=True)
    frappe.db.commit()
    return doc.name


def _cleanup(doc_name: str, job_type: str = "test.hardening"):
    try:
        frappe.db.delete("SyncJobDLQ", {"sync_job": doc_name})
        frappe.db.delete("SyncJobCheckpoint", {"parent": doc_name})
        frappe.db.delete("SyncJobDedup", {"job_type": job_type})
        frappe.db.delete("SyncJob", doc_name)
        frappe.db.commit()
    except Exception:
        frappe.db.rollback()


# ── (a) _write_dlq failure propagation ─────────────────────────────────


class TestWriteDLQFailure(unittest.TestCase):
    """
    When the DLQ write fails, the exception must propagate out of
    run() and the record's dedup row must stay 'in_progress' — not
    'completed' — so the record is eligible for a later re-run.
    """

    def setUp(self):
        frappe.db.delete("SyncJobDedup", {"job_type": "test.hardening"})
        frappe.db.commit()
        self.doc_name = _make_sync_job_doc()

    def tearDown(self):
        _cleanup(self.doc_name)

    def test_dlq_insert_failure_propagates_and_dedup_not_completed(self):
        class AlwaysFailsJob(_StubJob):
            max_retries = 1

            def process_record(self, record):
                raise JobFailed("boom")

        job = AlwaysFailsJob(job_name=self.doc_name, total=1)
        record = {"id": "record-0", "index": 0}
        key = job._idempotency_key(record)

        with patch.object(
            job, "_write_dlq",
            side_effect=RuntimeError("DLQ insert failed"),
        ):
            # The RuntimeError must propagate out of run() — it is not
            # JobDeadLettered, so the record loop does not swallow it.
            with self.assertRaises(RuntimeError) as ctx:
                job.run()
            self.assertIn("DLQ insert failed", str(ctx.exception))

        # The record was claimed (in_progress) before retry_dlq ran,
        # but must NOT have been completed — complete_dedup only runs
        # in the JobDeadLettered handler, which was never reached.
        row = frappe.db.get_value(
            "SyncJobDedup",
            {"job_type": job.job_type, "idempotency_key": key.hex},
            ["status"],
            as_dict=True,
        )
        self.assertIsNotNone(row, "the claimed dedup row must exist")
        self.assertEqual(
            row.status, "in_progress",
            "dedup row must not be completed when the DLQ write fails",
        )


# ── (b) _publish_progress throttling ───────────────────────────────────


class TestPublishProgressThrottle(unittest.TestCase):
    """
    _publish_progress must publish at most once per
    _redis_publish_interval seconds — not once per record.
    """

    def setUp(self):
        self.doc_name = _make_sync_job_doc()

    def tearDown(self):
        _cleanup(self.doc_name)

    def test_publishes_at_most_once_per_throttle_interval(self):
        job = _StubJob(job_name=self.doc_name, total=100)

        fake_now = [1000.0]

        with patch.object(time, "monotonic", lambda: fake_now[0]):
            with patch.object(
                frappe, "publish_realtime",
            ) as mock_publish:
                # Advance the clock by less than the interval between
                # calls: 100 rapid publishes must collapse to exactly 1.
                for _ in range(100):
                    fake_now[0] += 0.01  # 1s total, interval is 1.0s
                    job._publish_progress(total=100)

                self.assertEqual(
                    mock_publish.call_count, 1,
                    "expected exactly one publish within the throttle "
                    "window, not one per record",
                )

                # Advancing past the interval unlocks exactly one more.
                fake_now[0] += job._redis_publish_interval
                job._publish_progress(total=100)
                self.assertEqual(mock_publish.call_count, 2)

                # And again within the window: still 2.
                fake_now[0] += 0.5
                job._publish_progress(total=100)
                self.assertEqual(mock_publish.call_count, 2)

    def test_publish_payload_shape(self):
        job = _StubJob(job_name=self.doc_name, total=4)
        job._processed = 2

        fake_now = [1000.0]
        with patch.object(time, "monotonic", lambda: fake_now[0]):
            with patch.object(frappe, "publish_realtime") as mock_publish:
                job._publish_progress(total=4)

        self.assertEqual(mock_publish.call_count, 1)
        _, kwargs = mock_publish.call_args
        self.assertEqual(kwargs["event"], "job_progress")
        msg = kwargs["message"]
        self.assertEqual(msg["job"], self.doc_name)
        self.assertEqual(msg["processed"], 2)
        self.assertEqual(msg["total"], 4)
        self.assertEqual(msg["percent"], 50.0)


# ── (c) retry_dlq negative max_retries ─────────────────────────────────


class TestRetryDLQValidation(unittest.TestCase):

    def setUp(self):
        self.doc_name = _make_sync_job_doc()

    def tearDown(self):
        _cleanup(self.doc_name)

    def test_negative_max_retries_raises_value_error(self):
        job = _StubJob(job_name=self.doc_name, total=1)
        processed: list[int] = []

        def recording_process(rec):
            processed.append(rec["index"])
            return None

        with self.assertRaises(ValueError) as ctx:
            retry_dlq(
                func=recording_process,
                job=job,
                record={"id": "record-0", "index": 0},
                max_retries=-1,
            )
        self.assertIn("max_retries must be non-negative", str(ctx.exception))

        # No record was processed — validation fires before any attempt.
        self.assertEqual(processed, [])

    def test_zero_max_retries_processes_exactly_once(self):
        """Boundary: max_retries=0 means one attempt, no retries."""
        job = _StubJob(job_name=self.doc_name, total=1)
        attempts: list[int] = []

        def flaky(rec):
            attempts.append(rec["index"])
            raise JobFailed("always fails")

        with self.assertRaises(JobDeadLettered):
            retry_dlq(
                func=flaky,
                job=job,
                record={"id": "record-0", "index": 0},
                max_retries=0,
            )
        self.assertEqual(attempts, [0], "max_retries=0 must attempt exactly once")


if __name__ == "__main__":
    unittest.main()
