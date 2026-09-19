"""
fde_component / hooks.py
========================
Frappe 16 hooks for SP-05 — Resumable Ingestion, Sync & Job Orchestration.

Worker backend: Redis/Valkey via python-rq (Frappe's built-in RQ integration).
Progress: frappe.publish_realtime — no polling.
"""

app_name = "fde_component"
app_title = "FDE Component"
app_publisher = "FDE"
app_description = "Resumable Ingestion, Sync & Job Orchestration (SP-05)"
app_icon = "octicon octicon-device-sync"
app_color = "blue"
app_email = ""
app_license = "LGPL-3.0"

# ── DocTypes ──────────────────────────────────────────────────────────────────
fixtures = [
    {"dt": "Custom Field", "filters": [["module", "=", "FDE Component"]]},
]

# ── Scheduled tasks ───────────────────────────────────────────────────────────
# Keyed by cron expression — each fires the enqueue function for that schedule.
# Individual SyncSchedule docs drive actual job creation at runtime.
scheduler_events = {
    "cron": {
        # Every 5 minutes: sweep active SyncSchedule docs and enqueue due jobs.
        "*/5 * * * *": [
            "fde_component.jobs.scheduler.enqueue_due_schedules",
        ],
    },
    "daily": [],
    "hourly": [],
    "weekly": [],
    "monthly": [],
}

# ── Worker registration ───────────────────────────────────────────────────────
# RQ queues this app contributes to.
# Start the worker with: bench worker --queue default,short
worker_queues = [
    "default",   # general-purpose sync jobs
    "short",     # quick checkpoint updates, DLQ transitions
]

# ── Document events ───────────────────────────────────────────────────────────
# SyncJob lifecycle hooks — keep status transitions consistent.
doc_events = {
    "SyncJob": {
        "before_insert": "fde_component.fde_component.doctypes.sync_job.sync_job.set_job_defaults",
    },
}

# ── Overrides ─────────────────────────────────────────────────────────────────
# Inject the Resume button into the SyncJob form toolbar via JS.
override_doctype_class = {
    "SyncJob": "fde_component.fde_component.doctypes.sync_job.sync_job.SyncJob",
}

# ── Includes (JS/CSS injected into desk pages) ────────────────────────────────
# sync_job.js → list view status filters + progress bar
#             → detail view: checkpoint timeline, DLQ tab, resume/re-drive buttons
doctype_js = {
    "SyncJob": "fde_component/fde_component/doctypes/sync_job/sync_job.js",
}

# ── API methods ───────────────────────────────────────────────────────────────
api = [
    "fde_component.api.redrive_from_dlq",
    "fde_component.api.resume_job",
]

# ── Portal / public pages ─────────────────────────────────────────────────────
# (none — this is a back-end / desk-only component)

# ── Permissions ───────────────────────────────────────────────────────────────
# SyncJob: read for all authenticated users, write for System Manager + FDE role.
# SyncJobDLQ / SyncJobDedup: read-only for most — re-drive is a server action.
# Applied via fixtures / role permission records.
