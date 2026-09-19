"""
fde_component.tests.test_dedup
================================
Idempotency key tests that do NOT require a live Frappe DB.
These run with plain pytest.
"""

from __future__ import annotations

import hashlib

import pytest

from fde_component.jobs.idempotency import IdempotencyKey, _build_key
from fde_component.jobs.exceptions import DedupDuplicate


TEST_JOB_TYPE = "test.idempotency"


def _make_key(source_id: str, content: str = "") -> IdempotencyKey:
    return IdempotencyKey(TEST_JOB_TYPE, source_id, content)


def _expected_hex(job_type: str, source_id: str, content: str = "") -> str:
    raw = f"{job_type}\x00{source_id}\x00{content}"
    return hashlib.sha256(raw.encode()).hexdigest()


class TestIdempotencyKey:
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

    def test_hex_matches_expected_sha256(self):
        key = _make_key("msg-1", "content")
        assert key.hex == _expected_hex(TEST_JOB_TYPE, "msg-1", "content")

    def test_bytes_safe_input(self):
        key = _make_key("msg-1", "data\x00with\x00nulls")
        assert len(key.hex) == 64
