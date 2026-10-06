"""
fde_component.tests.test_runner
===============================
Tests for the RQ job runner dispatch behavior.

Compatible with Frappe's unittest-based test runner.
"""

from __future__ import annotations

import unittest

import frappe
from fde_component.jobs.runner import run_job, JOB_REGISTRY
from fde_component.jobs.base import CheckpointedJob
from fde_component.jobs.exceptions import JobFailed


class _TrackingJob(CheckpointedJob):
    job_type = "test.runner_dispatch"
    max_retries = 0
    checkpoint_every = 1000
    checkpoint_seconds = 999.0
    processed_ids: list = []

    def iter_records(self, offset, redrive_key=None):
        for i in range(offset, 3):
            yield {"id": f"record-{i}", "index": i}

    def process_record(self, record):
        _TrackingJob.processed_ids.append(record["index"])


# Register so run_job can resolve the class
JOB_REGISTRY["test.runner_dispatch"] = _TrackingJob


class RunnerTestCase(unittest.TestCase):

    def _make_queued_job_doc(self) -> str:
        doc = frappe.get_doc({
            "doctype": "SyncJob",
            "job_type": "test.runner_dispatch",
            "status": "Queued",
            "total_records": 10,
            "processed": 0,
        })
        doc.insert(ignore_permissions=True)
        frappe.db.commit()
        return doc.name

    def _cleanup_job_doc(self, doc_name: str):
        try:
            frappe.delete_doc("SyncJob", doc_name, ignore_permissions=True)
            frappe.db.commit()
        except Exception:
            frappe.db.rollback()


class TestRunnerDispatch(RunnerTestCase):

    def test_running_job_is_not_dispatched_twice(self):
        """
        Atomic dispatch claim: if a job is already Running, a second
        run_job() call must not execute the job again.
        """
        doc_name = self._make_queued_job_doc()
        try:
            frappe.db.set_value("SyncJob", doc_name, {"status": "Running"})
            frappe.db.commit()

            run_job(doc_name, "test.runner_dispatch")

            doc = frappe.get_doc("SyncJob", doc_name)
            self.assertEqual(doc.status, "Running")
        finally:
            self._cleanup_job_doc(doc_name)

    def test_queued_job_can_be_dispatched(self):
        """
        A job in Queued status can be dispatched.
        The runner should attempt to run it (it will fail because
        no job class is registered, but the claim succeeds).
        """
        doc_name = self._make_queued_job_doc()
        try:
            with self.assertRaises(JobFailed):
                run_job(doc_name, "test.runner_dispatch")

            doc = frappe.get_doc("SyncJob", doc_name)
            self.assertEqual(doc.status, "Failed")
        finally:
            self._cleanup_job_doc(doc_name)

    def test_dead_lettered_job_is_not_dispatched(self):
        """
        A job in Dead Lettered status should not be dispatched.
        """
        doc_name = self._make_queued_job_doc()
        try:
            frappe.db.set_value("SyncJob", doc_name, {"status": "Dead Lettered"})
            frappe.db.commit()

            run_job(doc_name, "test.runner_dispatch")

            doc = frappe.get_doc("SyncJob", doc_name)
            self.assertEqual(doc.status, "Dead Lettered")
        finally:
            self._cleanup_job_doc(doc_name)

    def test_completed_job_is_not_dispatched(self):
        """
        A job in Completed status should not be dispatched.
        """
        doc_name = self._make_queued_job_doc()
        try:
            frappe.db.set_value("SyncJob", doc_name, {"status": "Completed"})
            frappe.db.commit()

            run_job(doc_name, "test.runner_dispatch")

            doc = frappe.get_doc("SyncJob", doc_name)
            self.assertEqual(doc.status, "Completed")
        finally:
            self._cleanup_job_doc(doc_name)

    def test_double_dispatch_second_call_skips_processing(self):
        """
        If run_job is called while the SyncJob is already Running, the
        second call must not process any records (atomic claim guard).
        """
        doc_name = self._make_queued_job_doc()
        try:
            _TrackingJob.processed_ids = []

            # First call: Queued → Running, processes all 3 records
            run_job(doc_name, "test.runner_dispatch")
            self.assertEqual(_TrackingJob.processed_ids, [0, 1, 2])

            # Simulate the job still being Running (e.g. two workers racing)
            frappe.db.set_value("SyncJob", doc_name, {"status": "Running"})
            frappe.db.commit()
            _TrackingJob.processed_ids = []

            # Second call: Running → claim fails (rowcount=0), no records processed
            run_job(doc_name, "test.runner_dispatch")
            self.assertEqual(_TrackingJob.processed_ids, [])
        finally:
            self._cleanup_job_doc(doc_name)
