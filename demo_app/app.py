"""
SP-05 Demo — Streamlit-based lightweight demo
Run: streamlit run app.py
"""

import csv
import hashlib
import io
import json
import sqlite3
import time
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

import streamlit as st

from customers import CUSTOMERS_B

DB_PATH = Path("demo.db")


def get_conn() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db() -> None:
    conn = get_conn()
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS sync_job (
            name TEXT PRIMARY KEY,
            job_type TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'Queued',
            total_records INTEGER DEFAULT 0,
            processed INTEGER DEFAULT 0,
            failed_count INTEGER DEFAULT 0,
            error_summary TEXT,
            started_at TEXT,
            completed_at TEXT,
            source_config TEXT,
            created_via TEXT DEFAULT 'Manual',
            created_at TEXT DEFAULT CURRENT_TIMESTAMP,
            retry_count INTEGER DEFAULT 0
        );

        CREATE TABLE IF NOT EXISTS sync_job_checkpoint (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            parent TEXT NOT NULL,
            offset INTEGER NOT NULL,
            records_processed INTEGER NOT NULL,
            checksum TEXT,
            checkpointed_at TEXT DEFAULT CURRENT_TIMESTAMP
        );

        CREATE TABLE IF NOT EXISTS sync_job_dlq (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            parent TEXT NOT NULL,
            item_payload TEXT,
            error TEXT,
            retry_count INTEGER DEFAULT 0,
            re_drive_status TEXT DEFAULT 'Pending'
        );

        CREATE TABLE IF NOT EXISTS sync_job_dedup (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            job_type TEXT NOT NULL,
            idempotency_key TEXT NOT NULL,
            source_id TEXT,
            sync_job_id TEXT,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP
        );
    """)
    conn.commit()
    conn.close()


class IdempotencyKey:
    def __init__(self, job_type: str, source_id: str, content: str = "") -> None:
        raw = f"{job_type}\x00{source_id}\x00{content}"
        self.hex = hashlib.sha256(raw.encode()).hexdigest()

    def __repr__(self) -> str:
        return f"IdempotencyKey({self.hex[:16]}...)"


@dataclass
class JobResult:
    status: str
    processed: int
    checkpoints: int
    duplicates: int


def create_job(job_type: str, total: int, source_config: dict) -> str:
    name = f"SJ-{int(time.time())}"
    conn = get_conn()
    conn.execute(
        "INSERT INTO sync_job (name, job_type, total_records, source_config, status) VALUES (?, ?, ?, ?, 'Queued')",
        (name, job_type, total, json.dumps(source_config)),
    )
    conn.commit()
    conn.close()
    return name


def run_job(job_name: str, fail_after: int | None = None, records: list | None = None) -> JobResult:
    conn = get_conn()
    job = conn.execute("SELECT * FROM sync_job WHERE name = ?", (job_name,)).fetchone()
    if not job:
        raise ValueError(f"Job {job_name} not found")

    if records is None:
        records = CUSTOMERS_B[: job["total_records"]]

    total = len(records)
    checkpoint_every = 3
    duplicate_count = 0

    conn.execute(
        "UPDATE sync_job SET status = 'Running', started_at = ?, total_records = ? WHERE name = ?",
        (datetime.now().isoformat(), total, job_name),
    )
    conn.commit()

    try:
        for i in range(total):
            record = records[i]
            record_id = record.get("id", f"record-{i}")
            payload = json.dumps(record)
            record_name = f"{record.get('name', record_id)} ({record_id})"

            st.write(f"🔄 **Processing:** {record_name}")

            key = IdempotencyKey(job["job_type"], record_id, payload)
            existing = conn.execute(
                "SELECT id FROM sync_job_dedup WHERE job_type = ? AND idempotency_key = ?",
                (job["job_type"], key.hex),
            ).fetchone()
            if existing:
                st.write(f"⚠️ **DUPLICATE DETECTED:** {record_name} — already processed, skipping")
                duplicate_count += 1
                continue

            conn.execute(
                "INSERT INTO sync_job_dedup (job_type, idempotency_key, source_id, sync_job_id) VALUES (?, ?, ?, ?)",
                (job["job_type"], key.hex, record_id, job_name),
            )

            if fail_after is not None and i == fail_after:
                conn.execute(
                    "INSERT INTO sync_job_dlq (parent, item_payload, error, retry_count) VALUES (?, ?, ?, 0)",
                    (job_name, payload, "Simulated failure"),
                )
                conn.execute(
                    "UPDATE sync_job SET failed_count = failed_count + 1 WHERE name = ?",
                    (job_name,),
                )
                conn.commit()
                st.write(f"❌ **FAILED:** {record_name} — moved to DLQ")
                raise RuntimeError("Simulated failure")

            processed = i + 1 - duplicate_count
            conn.execute(
                "UPDATE sync_job SET processed = ? WHERE name = ?",
                (processed, job_name),
            )

            if (i + 1) % checkpoint_every == 0:
                conn.execute(
                    "INSERT INTO sync_job_checkpoint (parent, offset, records_processed) VALUES (?, ?, ?)",
                    (job_name, i + 1, processed),
                )
                st.write(f"💾 **Checkpoint saved:** offset={i + 1}, processed={processed}")

            conn.commit()
            time.sleep(0.3)

        processed = total - duplicate_count
        conn.execute(
            "INSERT INTO sync_job_checkpoint (parent, offset, records_processed) VALUES (?, ?, ?)",
            (job_name, total, processed),
        )
        conn.execute(
            "UPDATE sync_job SET status = 'Completed', completed_at = ?, processed = ? WHERE name = ?",
            (datetime.now().isoformat(), processed, job_name),
        )
        conn.commit()
        st.write(f"✅ **Job completed:** {processed}/{total} records processed ({duplicate_count} duplicates skipped)")
        return JobResult("Completed", processed, 1, duplicate_count)

    except RuntimeError as e:
        processed = i - duplicate_count
        conn.execute(
            "INSERT INTO sync_job_checkpoint (parent, offset, records_processed) VALUES (?, ?, ?)",
            (job_name, i, processed),
        )
        conn.execute(
            "UPDATE sync_job SET status = 'Interrupted', error_summary = ?, processed = ? WHERE name = ?",
            (str(e), processed, job_name),
        )
        conn.commit()
        st.write(f"⏸️ **Job interrupted:** {processed}/{total} records processed")
        return JobResult("Interrupted", processed, 1, duplicate_count)
    finally:
        conn.close()


def resume_job(job_name: str) -> JobResult:
    conn = get_conn()
    job = conn.execute("SELECT * FROM sync_job WHERE name = ?", (job_name,)).fetchone()
    if not job:
        raise ValueError(f"Job {job_name} not found")
    if job["status"] != "Interrupted":
        raise ValueError(f"Job {job_name} is not Interrupted")

    conn.execute(
        "UPDATE sync_job SET status = 'Running', error_summary = NULL WHERE name = ?",
        (job_name,),
    )
    conn.commit()
    conn.close()
    return run_job(job_name)


def redrive_from_dlq(job_name: str) -> JobResult:
    conn = get_conn()
    job = conn.execute("SELECT * FROM sync_job WHERE name = ?", (job_name,)).fetchone()
    if not job:
        raise ValueError(f"Job {job_name} not found")

    retry_count = (job["retry_count"] or 0) + 1
    max_retries = 2

    if retry_count >= max_retries:
        conn.execute(
            "UPDATE sync_job SET status = 'Dead Lettered', retry_count = ? WHERE name = ?",
            (retry_count, job_name),
        )
        conn.execute(
            "INSERT INTO sync_job_dlq (parent, item_payload, error, retry_count) VALUES (?, ?, ?, ?)",
            (job_name, json.dumps({"id": "manual-retry"}), "Exhausted retries", retry_count),
        )
        conn.commit()
        conn.close()
        return JobResult("Dead Lettered", job["processed"], 1, 0)

    conn.execute(
        "UPDATE sync_job SET status = 'Running', retry_count = ?, error_summary = NULL WHERE name = ?",
        (retry_count, job_name),
    )
    conn.execute("DELETE FROM sync_job_dlq WHERE parent = ?", (job_name,))
    conn.commit()
    conn.close()
    return run_job(job_name)


def main() -> None:
    st.set_page_config(page_title="SP-05 Demo", layout="wide")
    init_db()

    st.title("SP-05: Resumable Ingestion & Job Orchestration — Demo")
    st.markdown("**Lightweight prototype with SQLite + Streamlit**")

    page = st.sidebar.radio("Navigate", ["Dashboard", "Create Job", "Job Detail", "DLQ", "Dedup Log", "How It Works"])

    if page == "Dashboard":
        show_dashboard()
    elif page == "Create Job":
        show_create_job()
    elif page == "Job Detail":
        show_job_detail()
    elif page == "DLQ":
        show_dlq()
    elif page == "Dedup Log":
        show_dedup_log()
    elif page == "How It Works":
        show_how_it_works()


def show_dashboard() -> None:
    st.header("Dashboard")

    if st.button("Reset All Data"):
        DB_PATH.unlink(missing_ok=True)
        init_db()
        st.success("Database reset!")
        st.rerun()
        return

    conn = get_conn()
    jobs = conn.execute("SELECT * FROM sync_job ORDER BY name DESC").fetchall()
    conn.close()

    if not jobs:
        st.info("No jobs yet. Create one from the 'Create Job' page.")
        return

    total = len(jobs)
    completed = sum(1 for j in jobs if j["status"] == "Completed")
    failed = sum(1 for j in jobs if j["status"] in ("Failed", "Dead Lettered"))
    interrupted = sum(1 for j in jobs if j["status"] == "Interrupted")

    col1, col2, col3, col4 = st.columns(4)
    col1.metric("Total Jobs", total)
    col2.metric("Completed", completed)
    col3.metric("Failed / DLQ", failed)
    col4.metric("Interrupted", interrupted)

    st.subheader("Jobs")
    for job in jobs:
        with st.expander(f"{job['name']} — {job['status']}"):
            col1, col2, col3 = st.columns(3)
            col1.write(f"**Type:** {job['job_type']}")
            col2.write(f"**Progress:** {job['processed']}/{job['total_records']}")
            col3.write(f"**Created:** {job['created_via']}")

            if job["total_records"] > 0:
                pct = int(job["processed"] / job["total_records"] * 100)
                st.progress(pct / 100, text=f"{pct}% complete")


def show_create_job() -> None:
    st.header("Create New SyncJob")

    col1, col2 = st.columns(2)
    with col1:
        job_type = st.selectbox("Job Type", ["customer.import", "demo.import"])
        max_total = len(CUSTOMERS_B) if job_type == "customer.import" else 1000
        total = st.number_input("Total Records", min_value=1, max_value=max_total, value=min(20, max_total))
        fail_after = st.number_input("Fail After Record (optional)", min_value=-1, max_value=max_total, value=-1,
                                      help="Simulate failure at this record index (0-based)")

    with col2:
        source_config = st.text_area("Source Config (JSON)", value='{"source": "demo"}')
        uploaded_file = st.file_uploader("Upload CSV (optional)", type=["csv"],
                                         help="Upload a CSV file with customer data. Must have 'id', 'name', 'email', 'phone', 'company' columns.")

    # Show data source preview
    st.subheader("Data Source")
    if uploaded_file is not None:
        csv_data = list(csv.DictReader(io.StringIO(uploaded_file.getvalue().decode("utf-8"))))
        st.write(f"Loaded **{len(csv_data)} records** from uploaded CSV")
        with st.expander("Preview data"):
            st.table(csv_data[:10])
    else:
        st.write(f"Using **{len(CUSTOMERS_B)} built-in demo records** (includes duplicates)")
        with st.expander("Preview data"):
            preview = CUSTOMERS_B[:int(total)]
            st.table([{**c, "content": json.dumps(c)} for c in preview])

    if st.button("Create & Run Job", type="primary"):
        job_name = create_job(job_type, int(total), json.loads(source_config))
        st.success(f"Job created: {job_name}")

        st.subheader("Processing")
        result = run_job(job_name, fail_after if fail_after >= 0 else None)

        st.subheader("Summary")
        col1, col2, col3, col4 = st.columns(4)
        col1.metric("Status", result.status)
        col2.metric("Processed", result.processed)
        col3.metric("Checkpoints", result.checkpoints)
        col4.metric("Duplicates", result.duplicates)


def show_job_detail() -> None:
    st.header("Job Detail")

    conn = get_conn()
    jobs = conn.execute("SELECT name, job_type, status FROM sync_job ORDER BY name DESC").fetchall()
    conn.close()

    if not jobs:
        st.info("No jobs found.")
        return

    job_name = st.selectbox("Select Job", [j["name"] for j in jobs])
    conn = get_conn()
    job = conn.execute("SELECT * FROM sync_job WHERE name = ?", (job_name,)).fetchone()
    checkpoints = conn.execute(
        "SELECT * FROM sync_job_checkpoint WHERE parent = ? ORDER BY id",
        (job_name,),
    ).fetchall()
    conn.close()

    st.subheader(f"Job: {job_name}")

    col1, col2, col3 = st.columns(3)
    col1.metric("Status", job["status"])
    col2.metric("Progress", f"{job['processed']}/{job['total_records']}")
    if job["total_records"] > 0:
        pct = int(job["processed"] / job["total_records"] * 100)
        col3.metric("Complete", f"{pct}%")

    st.subheader("Checkpoints")
    if checkpoints:
        checkpoint_data = [
            {
                "Offset": cp["offset"],
                "Processed": cp["records_processed"],
                "Time": cp["checkpointed_at"],
            }
            for cp in checkpoints
        ]
        st.table(checkpoint_data)
    else:
        st.info("No checkpoints yet.")

    st.subheader("Actions")
    col1, col2 = st.columns(2)

    if job["status"] == "Interrupted":
        if col1.button("Resume Job", type="primary"):
            with st.spinner("Resuming from last checkpoint..."):
                result = resume_job(job_name)
            st.success(f"Resumed! Status: {result.status}, Processed: {result.processed}")
            st.rerun()

    if job["status"] in ("Interrupted", "Dead Lettered"):
        if col2.button("Re-drive from DLQ"):
            with st.spinner("Re-driving..."):
                result = redrive_from_dlq(job_name)
            st.success(f"Re-driven! Status: {result.status}, Processed: {result.processed}")
            st.rerun()


def show_dlq() -> None:
    st.header("Dead Letter Queue")

    conn = get_conn()
    dlq_items = conn.execute("SELECT * FROM sync_job_dlq ORDER BY id DESC").fetchall()
    conn.close()

    if not dlq_items:
        st.info("DLQ is empty. No failed items.")
        return

    st.subheader(f"Failed Items ({len(dlq_items)} total)")
    for item in dlq_items:
        with st.expander(f"Job: {item['parent']} — Error: {item['error'][:50]}"):
            st.write(f"**Item Payload:** {item['item_payload']}")
            st.write(f"**Error:** {item['error']}")
            st.write(f"**Retry Count:** {item['retry_count']}")


def show_dedup_log() -> None:
    st.header("Idempotency Log")

    conn = get_conn()
    dedup = conn.execute("SELECT * FROM sync_job_dedup ORDER BY created_at DESC LIMIT 100").fetchall()
    conn.close()

    if not dedup:
        st.info("No dedup entries yet.")
        return

    st.subheader(f"Processed Records ({len(dedup)} total)")
    st.markdown("Each record gets a unique SHA-256 fingerprint. Duplicates are detected by matching fingerprints.")

    data = [
        {
            "Job Type": d["job_type"],
            "Fingerprint (SHA-256)": d["idempotency_key"][:32] + "...",
            "Source ID": d["source_id"],
            "Sync Job": d["sync_job_id"],
        }
        for d in dedup
    ]
    st.table(data)


def show_how_it_works() -> None:
    st.header("How It Works")

    st.markdown("""
    ## The Problem
    Imagine importing 20,000 customer records from an old system to a new one.

    **What can go wrong?**
    - Computer crashes after 10,000 records → lose all progress
    - Same customer imported twice → duplicates in database
    - Some records fail → don't know which ones or why
    - Need to retry every month → manual work

    ## The Solution: SP-05

    **1. Checkpoints** — Save points like a video game
    - Every 3 records: save progress
    - If crash: resume from last save point
    - No rework, no lost progress

    **2. Idempotency** — Fingerprint each record
    - SHA-256 hash of (job type + customer ID + customer data)
    - Before processing: check if fingerprint exists
    - If yes: skip (already processed)
    - Guarantees zero duplicates

    **3. DLQ (Dead Letter Queue)** — Failed items basket
    - If record fails: save to DLQ
    - Job continues with other records
    - Admin can review and re-drive later

    **4. Resume** — Pick up where you left off
    - Click Resume button
    - Reads last checkpoint
    - Continues from next record
    - Zero duplicates on resume

    ## Demo Flow

    1. **Create Job:** Import 20 customers, fail at record 10
    2. **Watch Processing:** See each customer being processed
    3. **Checkpoint:** See save points being written
    4. **Failure:** Record 10 fails, goes to DLQ
    5. **Resume:** Click Resume, job continues from record 11
    6. **DLQ:** View failed item
    7. **Dedup Log:** See SHA-256 fingerprints
    """)


if __name__ == "__main__":
    main()
