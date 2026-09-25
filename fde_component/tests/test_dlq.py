"""
fde_component.tests.test_dlq
==============================
DLQ retry, dead-lettering, and re-drive tests.
"""

from __future__ import annotations

import pytest

import frappe
from fde_component.jobs.base import CheckpointedJob
from fde_component.jobs.exceptions import JobFailed, JobDeadLettered


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
