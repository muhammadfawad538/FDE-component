"""
fde_component.tests.test_idempotency
=======================================
Idempotency key builder + dedup check/mark tests.

Test coverage:
1. IdempotencyKey determinism — same inputs always produce same hash
2. IdempotencyKey sensitivity to content — different content = different key
3. check_dedup raises DedupDuplicate for already-seen keys
4. mark_dedup creates a SyncJobDedup record
5. Sequential duplicate rejection — second mark_dedup is a no-op
6. Race-condition path — UniqueValidationError is caught cleanly

Concurrency note:
-----------------
Tests 5–6 are sequential.  True inter-process race safety (two OS processes
calling mark_dedup simultaneously) is guaranteed by the composite unique index
on (job_type, idempotency_key) added by patches.txt, not by pytest.  The
UniqueValidationError path in test 6 proves the error-handling code works;
the DB index is the actual concurrency guard.
"""

from __future__ import annotations

import hashlib
import unittest

import pytest

from fde_component.jobs.idempotency import (
    IdempotencyKey,
    check_dedup,
    claim_dedup,
    complete_dedup,
    is_deduped,
    mark_dedup,
)
from fde_component.jobs.exceptions import DedupDuplicate


# ── Helpers ──────────────────────────────────────────────────────────────────

TEST_JOB_TYPE = "test.idempotency"
TEST_SYNC_JOB = "SJ-TEST.001"  # fake name — mark_dedup stores it as a string


def _make_key(source_id: str, content: str = "") -> IdempotencyKey:
    return IdempotencyKey(TEST_JOB_TYPE, source_id, content)


def _expected_hex(job_type: str, source_id: str, content: str = "") -> str:
    raw = f"{job_type}\x00{source_id}\x00{content}"
    return hashlib.sha256(raw.encode()).hexdigest()


# ── Test 1: determinism ──────────────────────────────────────────────────────

class TestIdempotencyKeyDeterminism:
    def test_same_inputs_produce_same_digest(self):
        k1 = _make_key("msg-1", "hello")
        k2 = _make_key("msg-1", "hello")
        assert k1.hex == k2.hex

    def test_different_source_id_produces_different_digest(self):
        k1 = _make_key("msg-1")
        k2 = _make_key("msg-2")
        assert k1.hex != k2.hex

    def test_different_content_produces_different_digest(self):
        k1 = _make_key("msg-1", "v1")
        k2 = _make_key("msg-1", "v2")
        assert k1.hex != k2.hex

    def test_empty_content_is_stable(self):
        k1 = _make_key("msg-1", "")
        k2 = _make_key("msg-1", "")
        assert k1.hex == k2.hex

    def test_from_hex_roundtrip(self):
        original = _make_key("msg-1", "data")
        restored = IdempotencyKey.from_hex(original.hex)
        assert restored.hex == original.hex
        assert restored == original

    def test_str_returns_hex(self):
        key = _make_key("msg-1")
        assert str(key) == key.hex

    def test_hash_equality(self):
        k1 = _make_key("msg-1")
        k2 = _make_key("msg-1")
        assert hash(k1) == hash(k2)

    def test_equality_with_non_key(self):
        key = _make_key("msg-1")
        assert key != "not-a-key"


# ── Test 2: check_dedup / mark_dedup (requires live DB) ─────────────────────

@pytest.mark.doctest
class TestDedupOperations:
    """
    These tests require a live Frappe DB (run via `bench run-tests`).
    They create and clean up SyncJobDedup records.
    """

    def test_check_dedup_raises_on_seen_key(self):
        key = _make_key("msg-new")
        mark_dedup(TEST_JOB_TYPE, key, "msg-new", TEST_SYNC_JOB)
        with pytest.raises(DedupDuplicate):
            check_dedup(TEST_JOB_TYPE, key)

    def test_check_dedup_passes_on_new_key(self):
        key = _make_key(f"msg-{id(self)}")  # unique per test
        # should not raise
        check_dedup(TEST_JOB_TYPE, key)

    def test_mark_dedup_creates_record(self):
        key = _make_key(f"msg-create-{id(self)}")
        mark_dedup(TEST_JOB_TYPE, key, "msg-create", TEST_SYNC_JOB)
        assert is_deduped(TEST_JOB_TYPE, key) is True

    def test_sequential_duplicate_rejection(self):
        """
        Calling mark_dedup twice with the same key does not crash and does
        not create a second record.  The unique index guarantees at most one
        row; the second call catches UniqueValidationError and rolls back.
        """
        key = _make_key(f"msg-seq-{id(self)}")
        mark_dedup(TEST_JOB_TYPE, key, "msg-seq", TEST_SYNC_JOB)
        # second call — should be a no-op, not an error
        mark_dedup(TEST_JOB_TYPE, key, "msg-seq", TEST_SYNC_JOB)
        # still exactly one record
        count = frappe.db.count("SyncJobDedup", {
            "job_type": TEST_JOB_TYPE,
            "idempotency_key": key.hex,
        })
        assert count == 1

    def test_race_condition_unique_validation_error_caught(self):
        """
        Simulate a race by inserting directly, then calling mark_dedup —
        mark_dedup must catch UniqueValidationError and not raise.

        Note: this is a simulated race (sequential), not a true concurrent
        insert.  The unique index is the real concurrency guarantee; this
        test only proves the error-handling branch is reachable and correct.
        """
        import frappe

        key = _make_key(f"msg-race-{id(self)}")
        # Insert directly to simulate a concurrent worker winning the race
        doc = frappe.new_doc("SyncJobDedup")
        doc.job_type = TEST_JOB_TYPE
        doc.idempotency_key = key.hex
        doc.source_id = "race-simulator"
        doc.sync_job_id = TEST_SYNC_JOB
        doc.insert(ignore_permissions=True)
        frappe.db.commit()

        # Now mark_dedup must handle the duplicate gracefully
        mark_dedup(TEST_JOB_TYPE, key, "msg-race", TEST_SYNC_JOB)
        # No exception raised — test passes

    def test_different_job_types_are_independent(self):
        """
        The same source_id under different job_types should produce different
        dedup entries — the unique index is on (job_type, idempotency_key).
        """
        key_a = _make_key("msg-1")
        key_b = IdempotencyKey("other.job", "msg-1", "")

        mark_dedup(TEST_JOB_TYPE, key_a, "msg-1", TEST_SYNC_JOB)
        mark_dedup("other.job", key_b, "msg-1", "SJ-OTHER.001")
        # both exist independently
        assert is_deduped(TEST_JOB_TYPE, key_a) is True
        assert is_deduped("other.job", key_b) is True


class TestClaimOwnership(unittest.TestCase):
    """
    complete_dedup must only complete a record the caller
    claimed. A foreign claimed_by blocks completion; an
    empty/NULL claimed_by is treated as ours (legacy rows
    written before claimed_by existed).

    Requires a live Frappe DB (run via `bench run-tests`).
    """

    def setUp(self):
        import frappe
        frappe.db.delete("SyncJobDedup", {"job_type": TEST_JOB_TYPE})
        frappe.db.commit()

    def tearDown(self):
        import frappe
        frappe.db.delete("SyncJobDedup", {"job_type": TEST_JOB_TYPE})
        frappe.db.commit()

    def _insert_claim(self, key, claimed_by):
        """Insert an in_progress dedup row with a given claimed_by."""
        import frappe
        doc = frappe.new_doc("SyncJobDedup")
        doc.job_type = TEST_JOB_TYPE
        doc.idempotency_key = key.hex
        doc.source_id = key.hex[:8]
        doc.sync_job_id = TEST_SYNC_JOB
        doc.status = "in_progress"
        doc.claimed_by = claimed_by
        doc.insert(ignore_permissions=True)
        frappe.db.commit()

    def test_own_claim_completes(self):
        key = _make_key(f"own-{id(self)}")
        self._insert_claim(key, TEST_SYNC_JOB)
        self.assertTrue(complete_dedup(TEST_JOB_TYPE, key, TEST_SYNC_JOB))
        self.assertTrue(is_deduped(TEST_JOB_TYPE, key))

    def test_foreign_claim_not_completed(self):
        key = _make_key(f"foreign-{id(self)}")
        self._insert_claim(key, "SJ-OTHER-WORKER")
        # Another worker's claim must NOT be completed by us
        self.assertFalse(complete_dedup(TEST_JOB_TYPE, key, TEST_SYNC_JOB))
        # Row still in_progress, owned by the other worker
        import frappe
        status = frappe.db.get_value(
            "SyncJobDedup",
            {"job_type": TEST_JOB_TYPE, "idempotency_key": key.hex},
            "status",
        )
        self.assertEqual(status, "in_progress")

    def test_empty_claimed_by_treated_as_ours(self):
        key = _make_key(f"empty-{id(self)}")
        # Legacy row: claimed_by is NULL (backfilled rows, old patches)
        self._insert_claim(key, None)
        self.assertTrue(complete_dedup(TEST_JOB_TYPE, key, TEST_SYNC_JOB))
        self.assertTrue(is_deduped(TEST_JOB_TYPE, key))

    def test_no_sync_job_id_completes_legacy_row(self):
        key = _make_key(f"legacy-{id(self)}")
        self._insert_claim(key, "SJ-SOME-OTHER")
        # sync_job_id omitted (legacy mark_dedup path): ownership
        # check is skipped, preserving pre-existing behavior
        self.assertTrue(complete_dedup(TEST_JOB_TYPE, key))
        self.assertTrue(is_deduped(TEST_JOB_TYPE, key))

    def test_claim_and_complete_roundtrip(self):
        """Full lifecycle: claim as SJ-A, complete as SJ-A, verify
        the row is completed and no longer in_progress."""
        key = _make_key(f"roundtrip-{id(self)}")
        self.assertTrue(claim_dedup(TEST_JOB_TYPE, key, "msg-rt", "SJ-A"))
        self.assertTrue(complete_dedup(TEST_JOB_TYPE, key, "SJ-A"))
        self.assertTrue(is_deduped(TEST_JOB_TYPE, key))
        # A second completion attempt finds no in_progress row
        self.assertFalse(complete_dedup(TEST_JOB_TYPE, key, "SJ-A"))
