"""
fde_component.tests.test_scheduler
=====================================
Scheduler sweep tests (atomic slot claim).

Requires a live Frappe DB (run via `bench run-tests`).
"""

from __future__ import annotations

import unittest
from datetime import timedelta
from unittest.mock import patch

from frappe.utils import now_datetime

from fde_component.jobs.scheduler import _enqueue_schedule


TEST_JOB_TYPE = "test.scheduler"


class SchedulerTestCase(unittest.TestCase):
    """Base: creates real SyncSchedule fixtures."""

    def setUp(self):
        import frappe
        # Clean slate for this job type
        frappe.db.delete("SyncJob", {"job_type": TEST_JOB_TYPE})
        frappe.db.delete("SyncSchedule", {"job_type": TEST_JOB_TYPE})
        frappe.db.commit()

    def tearDown(self):
        import frappe
        # SyncJob rows first (they reference the schedule's
        # job_type), then the schedules — then one commit.
        frappe.db.delete("SyncJob", {"job_type": TEST_JOB_TYPE})
        frappe.db.delete("SyncSchedule", {"job_type": TEST_JOB_TYPE})
        frappe.db.commit()

    def _make_schedule(self, job_type: str = TEST_JOB_TYPE,
                       cron: str = "*/5 * * * *",
                       last_fired=None) -> str:
        """Create a real SyncSchedule and return its name."""
        import frappe
        doc = frappe.get_doc({
            "doctype": "SyncSchedule",
            "title": f"Test schedule {job_type}",
            "job_type": job_type,
            "cron": cron,
            "active": 1,
            "source_config": "{}",
        })
        if last_fired:
            doc.last_fired = last_fired
        doc.insert(ignore_permissions=True)
        frappe.db.commit()
        return doc.name

    def _get_sched(self, name: str) -> dict:
        """Fetch a schedule as the sweep would see it."""
        import frappe
        return frappe.get_all(
            "SyncSchedule",
            filters={"name": name},
            fields=["name", "job_type", "cron", "source_config", "last_fired"],
        )[0]


class TestEnqueueScheduleRace(SchedulerTestCase):
    """
    _enqueue_schedule must claim the schedule's slot
    atomically BEFORE creating the job, so two overlapping
    sweeps cannot both fire the same schedule.
    """

    def test_overlapping_sweeps_fire_once(self):
        """Two calls with the same stale sched dict must
        create exactly one SyncJob and one enqueue call."""
        import frappe
        previous = now_datetime() - timedelta(minutes=10)
        sched_name = self._make_schedule(last_fired=previous)

        # ONE sched dict carrying the OLD last_fired, as
        # the sweep saw it when it decided the schedule
        # was due. The same stale dict goes to both calls.
        sched = self._get_sched(sched_name)
        now = now_datetime()

        with patch("frappe.enqueue") as mock_enqueue:
            _enqueue_schedule(sched, now)
            # Second overlapping sweep - same stale sched
            _enqueue_schedule(sched, now)

        jobs = frappe.db.count("SyncJob", {"job_type": TEST_JOB_TYPE})
        self.assertEqual(jobs, 1, "overlapping sweeps must fire once")
        self.assertEqual(mock_enqueue.call_count, 1)

    def test_failed_enqueue_restores_last_fired(self):
        """If enqueue raises after the claim, the exception
        propagates, last_fired is restored, and no SyncJob
        is left behind."""
        import frappe
        previous = now_datetime() - timedelta(minutes=10)
        sched_name = self._make_schedule(last_fired=previous)
        sched = self._get_sched(sched_name)
        now = now_datetime()

        with patch("frappe.enqueue", side_effect=RuntimeError("enqueue failed")):
            with self.assertRaises(RuntimeError):
                _enqueue_schedule(sched, now)

        restored = frappe.db.get_value("SyncSchedule", sched_name, "last_fired")
        self.assertEqual(restored, previous)
        # The inserted SyncJob must be cleaned up
        self.assertEqual(frappe.db.count("SyncJob", {"job_type": TEST_JOB_TYPE}), 0)

    def test_same_job_type_two_schedules_distinct_names(self):
        """Two schedules with the same job_type fired in one
        sweep produce two distinct SyncJob names."""
        import frappe
        name_a = self._make_schedule(last_fired=now_datetime() - timedelta(minutes=10))
        name_b = self._make_schedule(last_fired=now_datetime() - timedelta(minutes=10))
        sched_a = self._get_sched(name_a)
        sched_b = self._get_sched(name_b)
        now = now_datetime()

        with patch("frappe.enqueue"):
            _enqueue_schedule(sched_a, now)
            _enqueue_schedule(sched_b, now)

        jobs = frappe.get_all(
            "SyncJob",
            filters={"job_type": TEST_JOB_TYPE},
            fields=["name"],
        )
        self.assertEqual(len(jobs), 2)
        self.assertNotEqual(jobs[0]["name"], jobs[1]["name"])


if __name__ == "__main__":
    unittest.main()
