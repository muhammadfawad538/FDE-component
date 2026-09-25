# SP-05 Demo

Lightweight Streamlit demo for **fde_component** — shows the four core SP-05 concepts in action using SQLite.

## What it demonstrates

| Concept | How it works in the demo |
|---------|--------------------------|
| **Checkpoints** | Every 3 records, progress is saved. If the job fails, you can resume from the last checkpoint — no records are reprocessed. |
| **Idempotency** | Each record gets a SHA-256 fingerprint. Before processing, the app checks if that fingerprint already exists. Duplicates are skipped automatically. |
| **DLQ (Dead Letter Queue)** | When a record fails, it's logged with the error and retry count. You can view all failed items and re-drive them. |
| **Resume** | After a failure, click Resume — the job picks up from the next unprocessed record. |

## Run it

```bash
cd demo_app
pip install streamlit
streamlit run app.py
```

## Demo data

- **20 unique customers** (Source A)
- **25 records with 5 duplicates mixed in** (Source B) — simulates a real re-import scenario where a CSV has repeated rows

## Pages

| Page | Purpose |
|------|---------|
| **Dashboard** | Overview of all jobs — total, completed, failed, interrupted |
| **Create Job** | Configure and run a new import. Set "Fail After Record" to simulate a failure mid-run. |
| **Job Detail** | See progress, checkpoints, and actions (Resume / Re-drive) for a specific job |
| **DLQ** | View all dead-lettered items with error details and retry counts |
| **Dedup Log** | See SHA-256 fingerprints for every processed record |
| **How It Works** | Explanation of the SP-05 problem and solution |

## Recommended demo flow

1. Go to **Create Job**
2. Set Total Records to 20, Fail After Record to 10
3. Click **Create & Run Job**
4. Watch the processing — see records 1-9 processed, record 10 fail and go to DLQ
5. Go to **Job Detail** — see the interrupted job with checkpoints at records 3, 6, 9
6. Click **Resume** — watch records 11-20 process, duplicates auto-skipped
7. Go to **DLQ** — see the failed item from record 10
8. Go to **Dedup Log** — see all 20 SHA-256 fingerprints (only 20, not 25, because 5 duplicates were skipped)
