"""
fde_component.tests.test_recovery
=====================================
Crash recovery tests: stalled-job recovery sweep
and self-claim reclaim on resume.

Requires a live Frappe DB (run via `bench run-tests`).
"""

from __future__ import annotations

import unittest
from unittest.mock import patch

from frappe.utils import add_to_date, now_datetime

from fde_component.jobs.idempotency import (
    IdempotencyKey,
    claim_dedup,
    reclaim_own_claims,
)
from fde_component.jobs.recovery import recover_stalled_jobs


TEST_JOB_TYPE = "test.recovery"


class RecoveryTestCase(unittest.TestCase):
    """Base: creates real SyncJob fixtures."""

    def setUp(self):
        import frappe
        frappe.db.delete("SyncJobCheckpoint", {"parent": ["like", "SJ-%"]})
        frappe.db.delete("SyncJobDedup", {"job_type": TEST_JOB_TYPE})
        frappe.db.delete("SyncJob", {"job_type": TEST_JOB_TYPE})
        frappe.db.commit()

    def tearDown(self):
        import frappe
        frappe.db.delete("SyncJobCheckpoint", {"parent": ["like", "SJ-%"]})
        frappe.db.delete("SyncJobDedup", {"job_type": TEST_JOB_TYPE})
        frappe.db.delete("SyncJob", {"job_type": TEST_JOB_TYPE})
        frappe.db.commit()

    def _make_job(self, status: str = "Running",
                  started_at=None) -> str:
        """Create a SyncJob and return its name."""
        import frappe
        doc = frappe.get_doc({
            "doctype": "SyncJob",
            "job_type": TEST_JOB_TYPE,
            "status": status,
            "total_records": 10,
            "processed": 0,
            "created_via": "API",
        })
        doc.insert(ignore_permissions=True)
        if started_at:
            frappe.db.set_value(
                "SyncJob", doc.name, "started_at", started_at)
        frappe.db.commit()
        return doc.name

    def _add_checkpoint(self, job_name: str, offset: int,
                         checkpointed_at=None) -> None:
        """Append a checkpoint row to a job."""
        import frappe
        doc = frappe.get_doc("SyncJob", job_name)
        row = {
            "offset": offset,
            "records_processed": offset,
            "checksum": "a" * 64,
        }
        if checkpointed_at:
            row["checkpointed_at"] = checkpointed_at
        doc.append("checkpoints", row)
        doc.save(ignore_permissions=True)
        frappe.db.commit()


class TestRecoverStalledJobs(RecoveryTestCase):
    """
    A Running job with no checkpoint for
    stale_after_minutes is moved to Interrupted;
    a healthy Running job is left alone.
    """

    def test_stalled_running_job_is_recovered(self):
        """A Running job whose newest checkpoint is
        older than the threshold becomes Interrupted."""
        import frappe
        stale_at = add_to_date(now_datetime(), minutes=-30)
        job_name = self._make_job(started_at=stale_at)
        self._add_checkpoint(job_name, 5, checkpointed_at=stale_at)

        with patch(
            "fde_component.jobs.recovery._stale_after_minutes",
            return_value=10,
        ):
            recovered = recover_stalled_jobs()

        self.assertEqual(recovered, 1)
        self.assertEqual(
            frappe.db.get_value("SyncJob", job_name, "status"),
            "Interrupted",
        )

    def test_healthy_running_job_not_recovered(self):
        """A Running job with a recent checkpoint is
        left Running."""
        import frappe
        job_name = self._make_job()
        self._add_checkpoint(job_name, 5)  # checkpointed now

        with patch(
            "fde_component.jobs.recovery._stale_after_minutes",
            return_value=10,
        ):
            recovered = recover_stalled_jobs()

        self.assertEqual(recovered, 0)
        self.assertEqual(
            frappe.db.get_value("SyncJob", job_name, "status"),
            "Running",
        )

    def test_never_checkpointed_stalled_job_recovered(self):
        """A Running job that never checkpointed but
        started long ago is recovered (falls back to
        started_at)."""
        import frappe
        stale_at = add_to_date(now_datetime(), minutes=-30)
        job_name = self._make_job(started_at=stale_at)
        # No checkpoint rows at all

        with patch(
            "fde_component.jobs.recovery._stale_after_minutes",
            return_value=10,
        ):
            recovered = recover_stalled_jobs()

        self.assertEqual(recovered, 1)
        self.assertEqual(
            frappe.db.get_value("SyncJob", job_name, "status"),
            "Interrupted",
        )

    def test_threshold_is_configurable(self):
        """The stale threshold is read from
        frappe.conf (sync_job_stale_after_minutes)."""
        import frappe
        with patch.object(
            frappe, "conf", {"sync_job_stale_after_minutes": 45},
        ):
            from fde_component.jobs.recovery import _stale_after_minutes
            self.assertEqual(_stale_after_minutes(), 45)

        # Default when unset
        with patch.object(frappe, "conf", {}):
            self.assertEqual(_stale_after_minutes(), 10)


class TestReclaimOwnClaims(RecoveryTestCase):
    """
    A resumed job reclaims its own stale in_progress
    rows so it can reprocess records it claimed but
    never completed — without losing or duplicating
    records.
    """

    def test_reclaim_own_claims_deletes_only_own_rows(self):
        """Only this job's in_progress rows are
        deleted; other jobs' rows survive."""
        import frappe
        job_name = self._make_job(status="Interrupted")
        other_job = self._make_job(status="Interrupted")

        key_own = IdempotencyKey(TEST_JOB_TYPE, "record-own", "")
        key_other = IdempotencyKey(TEST_JOB_TYPE, "record-other", "")
        key_done = IdempotencyKey(TEST_JOB_TYPE, "record-done", "")

        # Our own stale claim
        self.assertTrue(
            claim_dedup(TEST_JOB_TYPE, key_own, "record-own", job_name))
        # Another job's claim — must survive
        self.assertTrue(
            claim_dedup(TEST_JOB_TYPE, key_other, "record-other", other_job))
        # A completed row — must survive
        self.assertTrue(
            claim_dedup(TEST_JOB_TYPE, key_done, "record-done", job_name))
        from fde_component.jobs.idempotency import complete_dedup
        self.assertTrue(
            complete_dedup(TEST_JOB_TYPE, key_done, job_name))

        deleted = reclaim_own_claims(TEST_JOB_TYPE, job_name)
        self.assertEqual(deleted, 1)

        # Our own row is gone, so the record is reprocessable
        self.assertIsNone(
            frappe.db.get_value(
                "SyncJobDedup",
                {"job_type": TEST_JOB_TYPE,
                 "idempotency_key": key_own.hex},
                "status",
            )
        )
        # Other job's claim survives
        self.assertEqual(
            frappe.db.get_value(
                "SyncJobDedup",
                {"job_type": TEST_JOB_TYPE,
                 "idempotency_key": key_other.hex},
                "status",
            ),
            "in_progress",
        )
        # Completed row survives
        self.assertEqual(
            frappe.db.get_value(
                "SyncJobDedup",
                {"job_type": TEST_JOB_TYPE,
                 "idempotency_key": key_done.hex},
                "status",
            ),
            "completed",
        )

    def test_resumed_job_reprocesses_claimed_record(self):
        """End to end: a job killed after claiming a
        record (in_progress, never completed) is
        resumed; the record is processed exactly once
        — no loss, no duplicate."""
        from fde_component.jobs.base import CheckpointedJob

        import frappe
        job_name = self._make_job(status="Interrupted")

        processed: list[str] = []

        class ResumeJob(CheckpointedJob):
            job_type = TEST_JOB_TYPE
            max_retries = 1
            checkpoint_every = 1000
            checkpoint_seconds = 999.0

            def iter_records(self, offset, redrive_key=None):
                for i in range(offset, 3):
                    yield {"id": f"record-{i}", "index": i}

            def process_record(self, record):
                processed.append(record["id"])

        # Simulate the crash: record-0 was claimed but
        # never completed
        key_0 = IdempotencyKey(TEST_JOB_TYPE, "record-0", "")
        self.assertTrue(
            claim_dedup(TEST_JOB_TYPE, key_0, "record-0", job_name))

        # Recover the stalled job (moves it to Interrupted
        # — already Interrupted here) and resume
        job = ResumeJob(job_name=job_name)
        job.run()

        # record-0 was reclaimed and processed exactly once
        self.assertEqual(processed, ["record-0", "record-1", "record-2"])

        doc = frappe.get_doc("SyncJob", job_name)
        self.assertEqual(doc.status, "Completed")
        self.assertEqual(doc.processed, 3)

        # Every record has a completed dedup row
        rows = frappe.db.get_all(
            "SyncJobDedup",
            filters={"job_type": TEST_JOB_TYPE},
            fields=["idempotency_key", "status"],
        )
        self.assertEqual(len(rows), 3)
        for row in rows:
            self.assertEqual(row.status, "completed")


if __name__ == "__main__":
    unittest.main()
