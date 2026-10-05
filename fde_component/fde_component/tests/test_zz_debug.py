"""
Temporary debug test — DO NOT MERGE.
Creates a failing job and inspects checkpoint + DLQ state to trace
why status ends as "Completed" instead of "Dead Lettered", and why
checkpoint idx/offset values are wrong.
"""

import unittest

import frappe

from fde_component.jobs.base import CheckpointedJob
from fde_component.jobs.exceptions import JobFailed, JobDeadLettered


TOTAL_RECORDS = 5


class DebugFailingJob(CheckpointedJob):
    job_type = "test.debug_failing_job"
    max_retries = 1
    checkpoint_every = 2
    checkpoint_seconds = 5.0

    def __init__(self, job_name: str, total: int = TOTAL_RECORDS, **kwargs):
        super().__init__(job_name, **kwargs)
        self._total = total

    def iter_records(self, offset: int, redrive_key=None):
        for i in range(offset, self._total):
            yield {"id": f"record-{i}", "index": i}

    def process_record(self, record):
        raise JobFailed("Simulated failure")


class TestDebugDLQ(unittest.TestCase):

    def test_debug_failing_job_state(self):
        doc = frappe.get_doc({
            "doctype": "SyncJob",
            "job_type": "test.debug_failing_job",
            "status": "Queued",
            "total_records": TOTAL_RECORDS,
            "processed": 0,
        })
        doc.insert(ignore_permissions=True)
        frappe.db.commit()
        doc_name = doc.name

        try:
            # Monkeypatch save_checkpoint to print before/after DB state
            original_save_checkpoint = CheckpointedJob.save_checkpoint
            call_num = [0]

            def patched_save_checkpoint(self, offset):
                call_num[0] += 1
                before = frappe.db.get_value("SyncJob", self.job_name, "status")
                print(f"[save_checkpoint #{call_num[0]}] offset={offset} status_before={before}")
                result = original_save_checkpoint(self, offset)
                after = frappe.db.get_value("SyncJob", self.job_name, "status")
                print(f"[save_checkpoint #{call_num[0]}] offset={offset} status_after={after}")
                return result

            CheckpointedJob.save_checkpoint = patched_save_checkpoint

            try:
                job = DebugFailingJob(job_name=doc_name)
                job.run()
            finally:
                CheckpointedJob.save_checkpoint = original_save_checkpoint

            final_status = frappe.db.get_value("SyncJob", doc_name, "status")
            print(f"\n=== FINAL STATUS: {final_status} ===")

            checkpoints = frappe.get_all(
                "SyncJobCheckpoint",
                filters={"parent": doc_name},
                fields=["name", "idx", "offset", "records_processed", "checksum", "creation"],
                order_by="idx asc",
            )
            print(f"\n=== CHECKPOINTS ({len(checkpoints)} rows) ===")
            for cp in checkpoints:
                print(cp)

            dlq_count = frappe.db.count("SyncJobDLQ", {"sync_job": doc_name})
            print(f"\n=== DLQ COUNT: {dlq_count} ===")

            dlq_rows = frappe.get_all(
                "SyncJobDLQ",
                filters={"sync_job": doc_name},
                fields=["name", "status", "idempotency_key", "creation"],
            )
            for row in dlq_rows:
                print(row)

        finally:
            try:
                frappe.db.delete("SyncJobDLQ", {"sync_job": doc_name})
                frappe.db.delete("SyncJobCheckpoint", {"parent": doc_name})
                frappe.db.delete("SyncJob", doc_name)
                frappe.db.commit()
            except Exception:
                frappe.db.rollback()


if __name__ == "__main__":
    unittest.main()
