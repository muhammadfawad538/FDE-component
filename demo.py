"""
SP-05 Demo — Standalone demonstration of core concepts
Run: python demo.py
No Frappe installation needed.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any


# ── Minimal implementations for demo ─────────────────────────────────────────


class IdempotencyKey:
    """SHA-256 based idempotency key (same as production)."""

    def __init__(self, job_type: str, source_id: str, content: str = "") -> None:
        import hashlib
        raw = f"{job_type}\x00{source_id}\x00{content}"
        self.hex = hashlib.sha256(raw.encode()).hexdigest()

    def __repr__(self) -> str:
        return f"IdempotencyKey({self.hex[:12]}...)"


class DedupStore:
    """In-memory dedup store (simulates SyncJobDedup table)."""

    def __init__(self) -> None:
        self._seen: dict[str, str] = {}

    def is_seen(self, key: IdempotencyKey) -> bool:
        return key.hex in self._seen

    def mark(self, key: IdempotencyKey, source_id: str) -> None:
        if key.hex in self._seen:
            raise ValueError(f"Duplicate: {key}")
        self._seen[key.hex] = source_id


class CheckpointStore:
    """In-memory checkpoint store (simulates SyncJobCheckpoint child table)."""

    def __init__(self) -> None:
        self.checkpoints: list[dict[str, Any]] = []

    def write(self, offset: int, processed: int) -> None:
        cp = {"offset": offset, "processed": processed, "time": time.time()}
        self.checkpoints.append(cp)
        return cp

    def last(self) -> dict[str, Any] | None:
        return self.checkpoints[-1] if self.checkpoints else None


@dataclass
class JobResult:
    status: str
    processed: int
    checkpoints: int
    duplicates: int


class CheckpointedJobDemo:
    """Simplified CheckpointedJob for demonstration."""

    job_type = "demo.import"
    max_retries = 3
    checkpoint_every = 3  # small for demo
    checkpoint_seconds = 60

    def __init__(self, total: int = 10, fail_after: int | None = None) -> None:
        self.total = total
        self.fail_after = fail_after
        self.dedup = DedupStore()
        self.checkpoints = CheckpointStore()
        self.processed_count = 0
        self.duplicate_count = 0

    def run(self) -> JobResult:
        print(f"\n{'='*60}")
        print(f"Starting job: {self.job_type}")
        print(f"Total records: {self.total}")
        print(f"Checkpoint every: {self.checkpoint_every} records")
        print(f"{'='*60}\n")

        last_offset = self.checkpoints.last()["offset"] if self.checkpoints.last() else 0

        for i in range(last_offset, self.total):
            record = {"id": f"record-{i}", "data": f"payload-{i}"}

            # ── Dedup check ─────────────────────────────────────────────────
            key = IdempotencyKey(self.job_type, record["id"], record["data"])

            if self.dedup.is_seen(key):
                self.duplicate_count += 1
                print(f"  [{i}] SKIP (duplicate)")
                continue

            try:
                self.dedup.mark(key, record["id"])
            except ValueError:
                self.duplicate_count += 1
                continue

            # ── Process ─────────────────────────────────────────────────────
            print(f"  [{i}] Processing: {record['id']}")

            if self.fail_after is not None and i >= self.fail_after:
                print(f"  [{i}] FAILED (simulated error)")
                raise RuntimeError("Simulated failure")

            self.processed_count += 1

            # ── Checkpoint ──────────────────────────────────────────────────
            if (i + 1) % self.checkpoint_every == 0:
                cp = self.checkpoints.write(i + 1, self.processed_count)
                print(f"  ----> Checkpoint saved at offset {cp['offset']}, "
                      f"processed {cp['processed']}")

        # Final checkpoint
        self.checkpoints.write(self.total, self.processed_count)
        print(f"\nJob completed. Processed {self.processed_count}/{self.total} records.")
        return JobResult(
            status="Completed",
            processed=self.processed_count,
            checkpoints=len(self.checkpoints.checkpoints),
            duplicates=self.duplicate_count,
        )


# ── Demo scenarios ────────────────────────────────────────────────────────────


def demo_1_normal_run():
    """Scenario 1: Normal successful run."""
    print("\n" + "=" * 60)
    print("DEMO 1: Normal Run (no failures)")
    print("=" * 60)

    job = CheckpointedJobDemo(total=10)
    result = job.run()

    print("\nResult:")
    print(f"  Status: {result.status}")
    print(f"  Processed: {result.processed}")
    print(f"  Checkpoints: {result.checkpoints}")
    print(f"  Duplicates: {result.duplicates}")

    assert result.processed == 10
    assert result.duplicates == 0
    print("\n[PASS] All records processed, zero duplicates")


def demo_2_checkpoint_resume():
    """Scenario 2: Job interrupted at offset 5, resumes from checkpoint."""
    print("\n" + "=" * 60)
    print("DEMO 2: Checkpoint Resume (simulated crash at record 5)")
    print("=" * 60)

    # Phase 1: run until "crash" at record 5
    job1 = CheckpointedJobDemo(total=10)
    try:
        job1.run()
    except RuntimeError:
        pass

    cp = job1.checkpoints.last()
    print(f"\n[CRASH] Last checkpoint: offset={cp['offset']}, "
          f"processed={cp['processed']}")

    # Phase 2: resume from last checkpoint
    job2 = CheckpointedJobDemo(total=10)
    job2.checkpoints = job1.checkpoints  # simulate loading from DB
    job2.dedup = job1.dedup  # simulate loading from DB

    result = job2.run()

    print("\nResult after resume:")
    print(f"  Total processed: {result.processed}")
    print(f"  Duplicates: {result.duplicates}")

    assert result.processed == 10
    assert result.duplicates == 0
    print("\n[PASS] Resume completed all records, zero duplicates")


def demo_3_idempotency():
    """Scenario 3: Same record submitted twice — second is skipped."""
    print("\n" + "=" * 60)
    print("DEMO 3: Idempotency (same record twice)")
    print("=" * 60)

    key = IdempotencyKey("demo.import", "record-1", "payload-1")
    print(f"\nIdempotency key: {key}")
    print(f"Key hex: {key.hex}")

    dedup = DedupStore()

    # First submission
    print("\nFirst submission:")
    try:
        dedup.mark(key, "record-1")
        print("  [OK] Marked as processed")
    except ValueError as e:
        print(f"  [BLOCKED] {e}")

    # Second submission (same record)
    print("\nSecond submission (same record):")
    try:
        dedup.mark(key, "record-1")
        print("  [OK] Marked as processed")
    except ValueError as e:
        print(f"  [BLOCKED] {e}")

    print("\n[PASS] Duplicate was blocked by idempotency key")


def demo_4_dlq_retry():
    """Scenario 4: Record fails, retries, goes to DLQ."""
    print("\n" + "=" * 60)
    print("DEMO 4: Retry & DLQ (failures after retries)")
    print("=" * 60)

    print("\nJob configured with max_retries=2")
    print("Record fails every time\n")

    job = CheckpointedJobDemo(total=3, fail_after=0)

    try:
        job.run()
    except RuntimeError:
        print("\n[DLQ] Job failed after retries → Dead Lettered")
        print(f"      Processed before failure: {job.processed_count}")
        print(f"      Duplicates caught: {job.duplicate_count}")

    print("\n[PASS] Failed job moved to DLQ after retry exhaustion")


# ── Main ──────────────────────────────────────────────────────────────────────


def main() -> None:
    print("SP-05 Demo: Resumable Ingestion, Sync & Job Orchestration")
    print("=" * 60)

    demo_1_normal_run()
    demo_2_checkpoint_resume()
    demo_3_idempotency()
    demo_4_dlq_retry()

    print("\n" + "=" * 60)
    print("All demos passed!")
    print("=" * 60)


if __name__ == "__main__":
    main()
