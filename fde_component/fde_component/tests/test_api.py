"""
fde_component.tests.test_api
==============================
API endpoint contract tests.

Compatible with Frappe's unittest-based test runner.
"""

from __future__ import annotations

import json
import unittest

import frappe
import fde_component.api as api_module
from fde_component.jobs.idempotency import check_dedup, mark_dedup, IdempotencyKey
from fde_component.jobs.exceptions import DedupDuplicate


class TestAPIMethods(unittest.TestCase):

    def test_redrive_from_dlq_exists(self):
        self.assertTrue(hasattr(api_module, "redrive_from_dlq"))
        self.assertTrue(callable(api_module.redrive_from_dlq))

    def test_resume_job_exists(self):
        self.assertTrue(hasattr(api_module, "resume_job"))
        self.assertTrue(callable(api_module.resume_job))

    def test_redrive_from_dlq_signature(self):
        import inspect
        sig = inspect.signature(api_module.redrive_from_dlq)
        params = list(sig.parameters.keys())
        self.assertIn("job_name", params)

    def test_resume_job_signature(self):
        import inspect
        sig = inspect.signature(api_module.resume_job)
        params = list(sig.parameters.keys())
        self.assertIn("job_name", params)

    def test_redrive_from_dlq_clears_dedup_state(self):
        """
        Regression test: redrive_from_dlq must clear SyncJobDedup entries
        for the dead-lettered job, so the redriven job can reprocess its
        records instead of skipping them as duplicates.
        """
        job_name = "SJ-REDRIVE-TEST.002"
        try:
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

            key = IdempotencyKey("test.redrive", "record-0", "")
            mark_dedup("test.redrive", key, "record-0", job_name)
            frappe.db.commit()

            count_before = frappe.db.count("SyncJobDedup", {"sync_job_id": job_name})
            self.assertGreaterEqual(count_before, 1)

            dlq_doc = frappe.new_doc("SyncJobDLQ")
            dlq_doc.sync_job = job_name
            dlq_doc.payload = "{}"
            dlq_doc.reason = "test"
            dlq_doc.retry_count = 3
            dlq_doc.status = "Dead Lettered"
            dlq_doc.insert(ignore_permissions=True)
            frappe.db.commit()

            result = api_module.redrive_from_dlq(job_name)
            self.assertTrue(result["success"])

            count_after = frappe.db.count("SyncJobDedup", {"sync_job_id": job_name})
            self.assertEqual(count_after, 0)

            try:
                check_dedup("test.redrive", key)
            except DedupDuplicate:
                self.fail("Record should not be deduped after redrive")

            new_jobs = frappe.get_all(
                "SyncJob",
                filters={"job_type": "test.redrive", "status": "Queued"},
                fields=["name"],
            )
            self.assertGreaterEqual(len(new_jobs), 1)
        finally:
            try:
                for d in frappe.get_all("SyncJobDLQ", filters={"sync_job": job_name}, pluck="name"):
                    frappe.delete_doc("SyncJobDLQ", d, ignore_permissions=True)
                for d in frappe.get_all("SyncJob", filters={"job_type": "test.redrive"}, pluck="name"):
                    frappe.delete_doc("SyncJob", d, ignore_permissions=True)
                frappe.db.delete("SyncJobDedup", {"sync_job_id": job_name})
                frappe.db.commit()
            except Exception:
                frappe.db.rollback()
