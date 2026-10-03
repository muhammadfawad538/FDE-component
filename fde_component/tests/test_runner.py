"""
fde_component.tests.test_runner
===============================
Tests for the RQ job runner dispatch behavior.

Compatible with Frappe's unittest-based test runner.
"""

from __future__ import annotations

import unittest

import frappe
from fde_component.jobs.runner import run_job
from fde_component.jobs.exceptions import JobFailed


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
