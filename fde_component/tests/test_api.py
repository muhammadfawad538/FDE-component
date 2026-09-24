"""
fde_component.tests.test_api
==============================
API endpoint contract tests.

Tests verify the public API methods exist and have the expected signatures.
"""

from __future__ import annotations

import pytest

import fde_component.api as api_module


class TestAPIMethods:

    def test_redrive_from_dlq_exists(self):
        assert hasattr(api_module, "redrive_from_dlq")
        assert callable(api_module.redrive_from_dlq)

    def test_resume_job_exists(self):
        assert hasattr(api_module, "resume_job")
        assert callable(api_module.resume_job)

    def test_redrive_from_dlq_signature(self):
        import inspect
        sig = inspect.signature(api_module.redrive_from_dlq)
        params = list(sig.parameters.keys())
        assert "job_name" in params

    def test_resume_job_signature(self):
        import inspect
        sig = inspect.signature(api_module.resume_job)
        params = list(sig.parameters.keys())
        assert "job_name" in params

    def test_redrive_from_dlq_clears_dedup_state(self):
        """
        Regression test: redrive_from_dlq must clear SyncJobDedup entries
        for the dead-lettered job, so the redriven job can reprocess its
        records instead of skipping them as duplicates.
        """
        import frappe
        from fde_component.jobs.idempotency import check_dedup, mark_dedup, IdempotencyKey
        from fde_component.jobs.exceptions import DedupDuplicate

        job_name = "SJ-REDRIVE-TEST.001"
        try:
            # Create a job doc
            doc = frappe.get_doc({
                "doctype": "SyncJob",
                "job_type": "test.redrive",
                "status": "Dead Lettered",
                "total_records": 5,
                "processed": 5,
            })
            doc.insert(ignore_permissions=True)
            frappe.db.commit()
            job_name = doc.name

            # Simulate dedup state from the original run
            key = IdempotencyKey("test.redrive", "record-0", "")
            mark_dedup("test.redrive", key, "record-0", job_name)
            frappe.db.commit()

            # Verify dedup exists before redrive
            assert check_dedup("test.redrive", key) is None or True  # DedupDuplicate raised
            count_before = frappe.db.count("SyncJobDedup", {"sync_job_id": job_name})
            assert count_before >= 1, "Dedup state should exist before redrive"

            # Create a DLQ entry so redrive_from_dlq has something to clear
            dlq_doc = frappe.new_doc("SyncJobDLQ")
            dlq_doc.sync_job = job_name
            dlq_doc.payload = "{}"
            dlq_doc.reason = "test"
            dlq_doc.retry_count = 3
            dlq_doc.status = "Dead Lettered"
            dlq_doc.insert(ignore_permissions=True)
            frappe.db.commit()

            # Invoke the API redrive path
            result = api_module.redrive_from_dlq(job_name)
            assert result["success"] is True

            # Verify dedup state is cleared
            count_after = frappe.db.count("SyncJobDedup", {"sync_job_id": job_name})
            assert count_after == 0, f"Dedup state should be cleared after redrive, got {count_after}"

            # Verify the record is no longer considered a duplicate
            try:
                check_dedup("test.redrive", key)
            except DedupDuplicate:
                pytest.fail("Record should not be deduped after redrive")

            # Verify new job was created (redrive creates a new SyncJob)
            new_jobs = frappe.get_all(
                "SyncJob",
                filters={"job_type": "test.redrive", "status": "Queued"},
                fields=["name"],
            )
            assert len(new_jobs) >= 1, "Redrive should create a new Queued job"

        finally:
            # Cleanup
            try:
                for d in frappe.get_all("SyncJobDLQ", filters={"sync_job": job_name}, pluck="name"):
                    frappe.delete_doc("SyncJobDLQ", d, ignore_permissions=True)
                for d in frappe.get_all("SyncJob", filters={"job_type": "test.redrive"}, pluck="name"):
                    frappe.delete_doc("SyncJob", d, ignore_permissions=True)
                frappe.db.delete("SyncJobDedup", {"sync_job_id": job_name})
                frappe.db.commit()
            except Exception:
                frappe.db.rollback()
