# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

`fde_component` is a Frappe 16 app implementing SP-05: Resumable Ingestion, Sync & Job Orchestration.
It provides checkpointed sync jobs, idempotency, retry/DLQ semantics, bulk import with live progress,
and incremental sync scheduling — all as reusable building blocks for Frappe-based data pipelines.

## Stack and conventions

- **Framework:** Frappe 16 (bench-managed)
- **Python:** 3.10+
- **Queue backend:** Redis/Valkey via python-RQ (Frappe's built-in RQ integration) — not Redis Streams
- **Progress updates:** `frappe.publish_realtime` — no polling anywhere
- **License:** LGPL-3.0

## Running the app

All commands assume you're inside a bench directory and this app is installed:

```bash
# Start bench (Frappe + Redis + MariaDB)
bench start

# Open a bench console
bench console

# Run Frappe tests for this app only
bench --site <site-name> run-tests --app fde_component

# Run a single test file
bench --site <site-name> run-tests --module fde_component.tests.test_idempotency

# Start an RQ worker (required for job execution)
bench worker --queue default,short

# Migrate / install DocTypes after editing JSON
bench --site <site-name> migrate
```

## Architecture

### DocType contracts (source of truth)

Four DocTypes form the persistent state layer — their `json` definitions live under
`fde_component/doctypes/<name>/<name>.json`:

- **SyncJob** — master job record (status, counts, timestamps, high-watermark)
- **SyncJobCheckpoint** — child table: ordered seq/offset rows that let a worker resume
- **SyncJobDLQ** — dead-lettered items with re-drive status and audit fields
- **SyncJobDedup** — idempotency entries, unique DB index on `(job_type, idempotency_key)`
  The composite unique index is created by a patch in `patches.txt`, not by DocType JSON
  (which can only express single-field unique). The patch runs after DocType sync creates
  the table, checks `SHOW INDEX` first, and is idempotent — safe to re-run.

`SyncJob.status` lifecycle: `Queued → Running → Interrupted → Completed | Failed | Dead Lettered`

### Job layer (`fde_component/jobs/`)

- **base.py** — `CheckpointedJob` (abstract RQ-job-compatible base) and `retry_dlq` (decorator).
  `retry_dlq` is the single unit for retry + DLQ: on exhaustion it writes `SyncJobDLQ` and
  sets the parent `SyncJob` to `Dead Lettered`. Job code must never call DLQ manually.
- **idempotency.py** — `IdempotencyKey` (SHA-256 over `job_type + source_id + content`) and
  `check_dedup` / `mark_dedup`. `mark_dedup` inserts a `SyncJobDedup` doc; if two workers
  race, `frappe.db.UniqueValidationError` is caught and treated as "already seen." No
  `ignore_if_duplicate` parameter — that is not a Frappe API.
- **scheduler.py** — `enqueue_due_schedules`, called by the cron hook every 5 minutes.
- **exceptions.py** — `JobInterrupted`, `JobFailed`, `JobDeadLettered`, `DedupDuplicate`.
  These are the only exceptions job code should raise; the base class and decorator handle them.

### Worker and signal handling

- `hooks.py` registers the worker queues and the scheduler cron.
- Worker picks up `SyncJob` docs with status `Queued` or `Interrupted`, resumes from the last
  `SyncJobCheckpoint` row.
- SIGTERM/SIGINT raises `JobInterrupted`, which checkpoints and sets status to `Interrupted`
  before the worker exits.

### Frontend

- List view: `sync_job.js` — status filters, progress bar
- Detail view: `sync_job.js` — checkpoint timeline, DLQ tab, Resume / Re-drive buttons
- All live progress via `frappe.publish_realtime` — no HTTP polling

## Key rules

- Queue is always RQ via Frappe — never introduce Redis Streams, Celery, or other backends.
- DLQ writes happen only inside `retry_dlq`; job code must not call them directly.
- Dedup check uses `SyncJobDedup` DocType with a unique DB index on `(job_type, idempotency_key)`;
  concurrent races are handled by catching `frappe.db.UniqueValidationError`, not by any
  `ignore_if_duplicate` parameter.
- Checkpoints use the child table, not Redis — they must survive worker restarts.
- Checkpoint cadence: every 1,000 records or 30 seconds, whichever fires first.
- DLQ retries: 3 attempts by default, configurable per job type via the `max_retries` attribute
  on `CheckpointedJob` subclasses.

## Scale target

~5 million records ("years of mail") — bulk imports are expected to hit this volume, so chunk
sizing, memory limits, and checkpoint frequency should be chosen with that in mind.

## Milestone status

- **M1** — App scaffold, DocTypes, base class, idempotency, scheduler: code complete
- **M2** — Worker integration, retry/DLQ, conformance tests: **code complete, tests written, execution blocked — no bench environment available in this session**. M2 does not close until `test_checkpoint_resume.py` and `test_idempotency.py` have been run against a real bench site and produced genuine passing results. Do not start M3 until M2 is verified.
- **M3** — Progress/resume desk UI: not started
- **M4** — Incremental sync scheduling: not started
- **M5** — Full conformance suite: not started
