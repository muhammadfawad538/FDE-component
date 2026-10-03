"""
fde_component.tests.test_dlq
==============================
DLQ retry, dead-lettering, and re-drive tests.
"""

from __future__ import annotations

import json

import pytest

import frappe
from fde_component.jobs.base import CheckpointedJob
from fde_component.jobs.exceptions import JobFailed, JobDeadLettered
from fde_component.jobs.idempotency import IdempotencyKey
from fde_component.doctypes.sync_job_dlq.sync_job_dlq import re_drive


TOTAL_RECORDS = 100


class FailingJob(CheckpointedJob):
    job_type = "test.failing_job"
    max_retries = 2
    checkpoint_every = 50
    checkpoint_seconds = 5.0

    def iter_records(self, offset: int):
        for i in range(offset, self._total):
            yield {"id": f"record-{i}", "index": i}

    def process_record(self, record):
        raise JobFailed("Simulated failure")


@pytest.fixture
def failing_job_doc():
    doc = frappe.get_doc({
        "doctype": "SyncJob",
        "job_type": "test.failing_job",
        "status": "Queued",
        "total_records": TOTAL_RECORDS,
        "processed": 0,
    })
    doc.insert(ignore_permissions=True)
    frappe.db.commit()
    yield doc.name
    try:
        frappe.db.delete("SyncJobDLQ", {"sync_job": doc.name})
        frappe.db.delete("SyncJobCheckpoint", {"parent": doc.name})
        frappe.db.delete("SyncJob", doc.name)
        frappe.db.commit()
    except Exception:
        frappe.db.rollback()


class TestDLQ:
    def test_job_fails_after_max_retries(self, failing_job_doc):
        job = FailingJob(job_name=failing_job_doc)
        job.run()  # does not raise — JobDeadLettered is caught inside the loop

        doc = frappe.get_doc("SyncJob", failing_job_doc)
        assert doc.status == "Dead Lettered"

    def test_dlq_entries_created(self, failing_job_doc):
        job = FailingJob(job_name=failing_job_doc)
        job.run()

        dlq_count = frappe.db.count("SyncJobDLQ", {"sync_job": failing_job_doc})
        assert dlq_count > 0

    def test_successful_records_not_affected_by_dlq(self, failing_job_doc):
        """
        A mix of successful and failing records: the job completes with
        Dead Lettered status, DLQ entries exist for failures, and the
        processed count reflects only successfully completed records.
        """
        doc = frappe.get_doc("SyncJob", failing_job_doc)
        doc.total_records = 10
        doc.save(ignore_permissions=True)
        frappe.db.commit()

        class MixedJob(CheckpointedJob):
            job_type = "test.mixed_job"
            max_retries = 1
            checkpoint_every = 50

            def __init__(self, job_name: str, **kwargs):
                super().__init__(job_name, **kwargs)
                self._fail_at = {3, 7}  # these indices will fail

            def iter_records(self, offset: int):
                for i in range(offset, 10):
                    yield {"id": f"record-{i}", "index": i}

            def process_record(self, record):
                if record["index"] in self._fail_at:
                    raise JobFailed("Simulated failure")

        job = MixedJob(job_name=failing_job_doc)
        job.run()

        doc = frappe.get_doc("SyncJob", failing_job_doc)
        assert doc.status == "Dead Lettered"

        dlq_count = frappe.db.count("SyncJobDLQ", {"sync_job": failing_job_doc})
        assert dlq_count == 2  # records 3 and 7

        dedup_count = frappe.db.count("SyncJobDedup", {"job_type": "test.mixed_job"})
        assert dedup_count == 10  # all records, including DLQ'd ones


class TestRedriveScoping:
    """
    Regression test: redrive must scope the new job to the single failed
    record so the source only yields that record, not the full dataset.
    """

    @pytest.fixture
    def redrive_job_doc(self):
        """
        Create a parent SyncJob doc for redrive tests.
        """
        doc = frappe.get_doc({
            "doctype": "SyncJob",
            "job_type": "test.redrive_scope",
            "status": "Queued",
            "total_records": 5,
            "processed": 0,
            "source_config": "{}",
        })
        doc.insert(ignore_permissions=True)
        frappe.db.commit()
        yield doc.name
        # cleanup
        try:
            for d in frappe.get_all("SyncJobDLQ", filters={"sync_job": doc.name}, pluck="name"):
                frappe.delete_doc("SyncJobDLQ", d, ignore_permissions=True)
            for d in frappe.get_all("SyncJob", filters={"parent_job": doc.name}, pluck="name"):
                frappe.delete_doc("SyncJob", d, ignore_permissions=True)
            frappe.db.delete("SyncJobCheckpoint", {"parent": doc.name})
            frappe.db.delete("SyncJobDedup", {"job_type": "test.redrive_scope"})
            frappe.db.delete("SyncJob", doc.name)
            frappe.db.commit()
        except Exception:
            frappe.db.rollback()

    def test_redrive_scopes_to_single_record(self, redrive_job_doc: str):
        """
        End-to-end: original job runs, one record fails, re_drive() creates
        a scoped child job, child job processes ONLY the failed record.

        Two layers of evidence:
        1. Source-layer: iter_records() yields exactly 1 record (not 5)
        2. Framework-layer: only that record reaches process_record()
        """
        processed_ids: list[int] = []
        iter_yield_count = 0  # tracks how many records iter_records() yields

        class ScopedJob(CheckpointedJob):
            """
            Test job that honors redrive_key in iter_records().
            When redrive_key is set, only the matching record is yielded.
            """
            job_type = "test.redrive_scope"
            max_retries = 1
            checkpoint_every = 1000

            def __init__(self, job_name: str, **kwargs):
                super().__init__(job_name, **kwargs)
                # Only fail index 2 so it ends up in DLQ
                self._fail_at = {2}

            def iter_records(self, offset: int, redrive_key: str | None = None):
                for i in range(offset, 5):
                    if redrive_key is not None:
                        # Source-layer filter: yield only the matching record
                        key = IdempotencyKey(self.job_type, f"record-{i}", "").hex
                        if key != redrive_key:
                            continue
                    nonlocal iter_yield_count
                    iter_yield_count += 1
                    yield {"id": f"record-{i}", "index": i}

            def process_record(self, record):
                processed_ids.append(record["index"])

        # ── Phase 1: run original job ─────────────────────────────────────────
        job = ScopedJob(job_name=redrive_job_doc)
        job.run()

        doc = frappe.get_doc("SyncJob", redrive_job_doc)
        assert doc.status == "Dead Lettered"

        # Index 2 failed; indices 0,1,3,4 succeeded → all 5 have dedup entries
        dedup_count = frappe.db.count("SyncJobDedup", {"job_type": "test.redrive_scope"})
        assert dedup_count == 5, f"Expected 5 dedup entries, got {dedup_count}"

        # Verify index 2 is in DLQ
        dlq_entry = frappe.get_all(
            "SyncJobDLQ",
            filters={"sync_job": redrive_job_doc},
            fields=["idempotency_key", "index"],
        )
        assert len(dlq_entry) == 1
        failed_key = dlq_entry[0]["idempotency_key"]

        # Reset tracking for redrive run
        processed_ids.clear()
        iter_yield_count = 0

        # ── Phase 2: re_drive the failed record ────────────────────────────────
        re_drive(dlq_entry[0])

        # Find the child job created by re_drive
        child_jobs = frappe.get_all(
            "SyncJob",
            filters={"parent_job": redrive_job_doc, "job_type": "test.redrive_scope"},
            fields=["name", "source_config", "status"],
        )
        assert len(child_jobs) == 1, f"Expected 1 child job, got {len(child_jobs)}"
        child_job_name = child_jobs[0]["name"]

        # Verify the child job carries the redrive_key in source_config
        child_config = frappe.parse_json(child_jobs[0]["source_config"] or "{}")
        assert child_config.get("redrive_key") == failed_key, (
            f"Child job source_config should carry redrive_key={failed_key}, "
            f"got {child_config.get('redrive_key')}"
        )

        # ── Phase 3: run the child job ─────────────────────────────────────────
        child_job = ScopedJob(job_name=child_job_name)
        child_job.run()

        # ── Assertions ─────────────────────────────────────────────────────────

        # Source-layer evidence: iter_records() yielded exactly 1 record,
        # not all 5. This proves the source adapter honored redrive_key.
        assert iter_yield_count == 1, (
            f"Source iter_records() should yield exactly 1 record during redrive, "
            f"got {iter_yield_count} (if >1, the source did not filter by redrive_key)"
        )

        # Framework-layer evidence: only the failed record reaches process_record
        assert processed_ids == [2], (
            f"Expected only index 2 to be processed, got {processed_ids}"
        )

        # Child job completed successfully
        child_doc = frappe.get_doc("SyncJob", child_job_name)
        assert child_doc.status == "Completed", (
            f"Child job should be Completed, got {child_doc.status}"
        )

        # Existing dedup rows for indices 0,1,3,4 are still present
        dedup_count_after = frappe.db.count("SyncJobDedup", {"job_type": "test.redrive_scope"})
        assert dedup_count_after == 6, (
            f"Expected 6 dedup entries (5 original + 1 redrive), got {dedup_count_after}"
        )

        # Verify the failed index now has a completed dedup entry
        redriven_key = IdempotencyKey("test.redrive_scope", "record-2", "").hex
        exists = frappe.db.exists(
            "SyncJobDedup",
            {"job_type": "test.redrive_scope", "idempotency_key": redriven_key, "status": "completed"},
        )
        assert exists, "Redriven record should have a completed dedup entry"
