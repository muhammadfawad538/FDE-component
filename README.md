# fde_component

Frappe 16 app implementing **SP-05: Resumable Ingestion, Sync & Job Orchestration**.

Provides checkpointed sync jobs, idempotency, retry/DLQ semantics, bulk import with live progress, and incremental sync scheduling — all as reusable building blocks for Frappe-based data pipelines.

## Architecture

Four DocTypes form the persistent state layer:

| DocType | Role |
|---------|------|
| `SyncJob` | Master job record: status, counts, timestamps, high-watermark |
| `SyncJobCheckpoint` | Child table: ordered seq/offset rows for worker resume |
| `SyncJobDLQ` | Dead-lettered items with re-drive status and audit fields |
| `SyncJobDedup` | Idempotency entries with a composite unique index on `(job_type, idempotency_key)` |

### Job lifecycle

```
Queued → Running → Interrupted → Completed | Failed | Dead Lettered
```

### Core modules (`fde_component/jobs/`)

- **`base.py`** — `CheckpointedJob` (abstract base) and `retry_dlq` (retry + DLQ unit). `retry_dlq` is the single entry point for dead-lettering — job code must never call it directly.
- **`idempotency.py`** — `IdempotencyKey` (SHA-256 over `job_type + source_id + content`), `check_dedup` / `mark_dedup` with race-condition safety via the unique DB index.
- **`scheduler.py`** — `enqueue_due_schedules`, called by cron every 5 minutes.
- **`exceptions.py`** — `JobInterrupted`, `JobFailed`, `JobDeadLettered`, `DedupDuplicate`. These are the only exceptions job code should raise.

### Key guarantees

- **Resumable**: checkpoints written every 1,000 records or 30 seconds, whichever fires first. Survives worker restarts.
- **Idempotent**: composite unique index on `(job_type, idempotency_key)`. Concurrent races resolved by catching `frappe.db.UniqueValidationError`.
- **Reliable**: each record retries up to `max_retries` times (default 3, configurable per job class); exhausted items go to DLQ.
- **Progress**: `frappe.publish_realtime` — no HTTP polling.

## Prerequisites

- Frappe 16 bench (MariaDB + Redis running)
- Python 3.10+

## Installation

```bash
# From your bench directory
bench get-app fde_component https://github.com/muhammadfawad538/FDE-component.git
bench --site <your-site> install-app fde_component
bench --site <your-site> migrate
```

## Usage

### Define a job

```python
from fde_component.jobs.base import CheckpointedJob

class MailImportJob(CheckpointedJob):
    job_type = "mail.import"
    max_retries = 3

    def iter_records(self, offset: int):
        return mail_source.iter(offset=offset, limit=self.batch_size)

    def process_record(self, record):
        return ingest_mail(record)
```

### Enqueue

```python
frappe.enqueue(
    "fde_component.jobs.runner.run_job",
    queue="default",
    sync_job_name="SJ-00001",
    job_type="mail.import",
)
```

### Resume an interrupted job

```python
from fde_component.api import resume_job
resume_job("SJ-00001")
```

### Re-drive from DLQ

```python
from fde_component.api import redrive_from_dlq
redrive_from_dlq("SJ-00001")
```

## Milestones

| Milestone | Status |
|-----------|--------|
| M1 — App scaffold, DocTypes, base class, idempotency, scheduler | Complete |
| M2 — Worker integration, retry/DLQ, conformance tests | Complete |
| M3 — Progress/resume desk UI | Not started |
| M4 — Incremental sync scheduling | Not started |
| M5 — Full conformance suite | Not started |

## Testing

Tests require a bench-managed Frappe site:

```bash
bench --site <site-name> run-tests --app fde_component

# Run a specific test module
bench --site <site-name> run-tests --module fde_component.tests.test_idempotency
```

### Test categories

| File | Scope | Requires Frappe DB |
|------|-------|--------------------|
| `test_conformance.py` | SP-05 requirement contracts | No |
| `test_dedup.py` | Idempotency key determinism | No |
| `test_api.py` | API endpoint contracts | No |
| `test_dlq.py` | DLQ retry and dead-lettering | Yes |
| `test_idempotency.py` | Dedup DB operations | Yes |
| `test_checkpoint_resume.py` | Worker kill and resume acceptance test | Yes |

### Docker testing

A Dockerfile and docker-compose are provided for isolated test runs:

```bash
docker compose up --build      # build and run tests
docker compose down -v         # clean up (removes volumes)
```

## License

LGPL-3.0
