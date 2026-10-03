"""
fde_component.tests.test_runner
===============================
Tests for the RQ job runner dispatch behavior.

Requires a live Frappe DB (run via `bench run-tests`).
"""

from __future__ import annotations

import pytest

import frappe
from fde_component.jobs.runner import run_job
from fde_component.jobs.exceptions import JobFailed


class TestRunnerDispatch:
    """
    Tests for runner.py job dispatch behavior.
    """

    @pytest.fixture
    def queued_job_doc(self):
        doc = frappe.get_doc({
            "doctype": "SyncJob",
            "job_type": "test.runner_dispatch",
            "status": "Queued",
            "total_records": 10,
            "processed": 0,
        })
        doc.insert(ignore_permissions=True)
        frappe.db.commit()
        yield doc.name
        try:
            frappe.delete_doc("SyncJob", doc.name, ignore_permissions=True)
            frappe.db.commit()
        except Exception:
            frappe.db.rollback()

    def test_running_job_is_not_dispatched_twice(self, queued_job_doc: str):
        """
        Atomic dispatch claim: if a job is already Running, a second
        run_job() call must not execute the job again.

        This test simulates the race by:
        1. Manually setting the job to Running
        2. Calling run_job() — it should detect the non-Queued status
           and return without executing the job.
        """
        # Simulate worker A already claimed the job
        frappe.db.set_value("SyncJob", queued_job_doc, {"status": "Running"})
        frappe.db.commit()

        # Worker B tries to dispatch — should skip
        run_job(queued_job_doc, "test.runner_dispatch")

        doc = frappe.get_doc("SyncJob", queued_job_doc)
        assert doc.status == "Running", (
            f"Job should remain Running, got {doc.status}"
        )

    def test_queued_job_can_be_dispatched(self, queued_job_doc: str):
        """
        A job in Queued status can be dispatched.
        The runner should attempt to run it (it will fail because
        no job class is registered, but the claim succeeds).
        """
        # This will fail at job_cls lookup, but the claim should succeed
        with pytest.raises(JobFailed):
            run_job(queued_job_doc, "test.runner_dispatch")

        doc = frappe.get_doc("SyncJob", queued_job_doc)
        assert doc.status == "Failed", (
            f"Job should be Failed after unknown job_type, got {doc.status}"
        )

    def test_dead_lettered_job_is_not_dispatched(self, queued_job_doc: str):
        """
        A job in Dead Lettered status should not be dispatched.
        """
        frappe.db.set_value("SyncJob", queued_job_doc, {"status": "Dead Lettered"})
        frappe.db.commit()

        run_job(queued_job_doc, "test.runner_dispatch")

        doc = frappe.get_doc("SyncJob", queued_job_doc)
        assert doc.status == "Dead Lettered", (
            f"Job should remain Dead Lettered, got {doc.status}"
        )

    def test_completed_job_is_not_dispatched(self, queued_job_doc: str):
        """
        A job in Completed status should not be dispatched.
        """
        frappe.db.set_value("SyncJob", queued_job_doc, {"status": "Completed"})
        frappe.db.commit()

        run_job(queued_job_doc, "test.runner_dispatch")

        doc = frappe.get_doc("SyncJob", queued_job_doc)
        assert doc.status == "Completed", (
            f"Job should remain Completed, got {doc.status}"
        )
