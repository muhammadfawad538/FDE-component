"""
CI-only: build doctype shim packages so Frappe's load_doctype_module()
can resolve scrub(DocType.name) to the correct folder/file.

Example: DocType "SyncJob" has folder sync_job/ but Frappe scrubs it
to "syncjob". This script creates:
    fde_component/doctype/syncjob/__init__.py
    fde_component/doctype/syncjob/syncjob.py

If sync_job/sync_job.py exists, the shim re-exports from it.
Otherwise the shim provides a plain Document fallback.

Runs against the CI copy only (never touches the repo).
"""

import json
import os
import re
import sys


def scrub(name: str) -> str:
    return re.sub(r"[^a-z0-9]", "", name.lower())


def pascal_case(snake: str) -> str:
    """sync_job -> SyncJob, sync_job_dedup -> SyncJobDedup"""
    return "".join(w.capitalize() for w in snake.split("_"))


def make_shims(pkg_root: str) -> None:
    """
    Parameters
    ----------
    pkg_root : str
        Path to the *app package* directory, e.g.
        fde_bench/apps/fde_component/fde_component
    """
    doctypes_dir = os.path.join(pkg_root, "doctypes")
    doctype_dir = os.path.join(pkg_root, "doctype")

    # Ensure doctype/ is a real directory (not a symlink to doctypes/)
    if os.path.islink(doctype_dir):
        os.remove(doctype_dir)
        os.makedirs(doctype_dir, exist_ok=True)

    # Ensure __init__.py exists in doctypes/
    open(os.path.join(doctypes_dir, "__init__.py"), "a").close()

    for folder in sorted(os.listdir(doctypes_dir)):
        folder_path = os.path.join(doctypes_dir, folder)
        if not os.path.isdir(folder_path):
            continue

        json_path = os.path.join(folder_path, f"{folder}.json")
        if not os.path.isfile(json_path):
            continue

        # Ensure __init__.py in the folder
        open(os.path.join(folder_path, "__init__.py"), "a").close()

        # Symlink doctype/<folder> -> ../doctypes/<folder> so sync still finds JSON
        symlink_path = os.path.join(doctype_dir, folder)
        if os.path.islink(symlink_path):
            os.remove(symlink_path)
        if not os.path.exists(symlink_path):
            os.symlink(f"../doctypes/{folder}", symlink_path)

        # Build shim path: doctype/<scrub>/
        with open(json_path) as fh:
            data = json.load(fh)
        doc_name = data.get("name", "")
        s = scrub(doc_name)

        if s == folder:
            # Folder name already matches scrubbed DocType name — no shim needed
            continue

        shim_dir = os.path.join(doctype_dir, s)
        if os.path.exists(shim_dir):
            # Already exists (e.g. from a previous run) — skip
            continue

        os.makedirs(shim_dir, exist_ok=True)
        open(os.path.join(shim_dir, "__init__.py"), "a").close()

        py_file = os.path.join(shim_dir, f"{s}.py")
        real_py = os.path.join(folder_path, f"{folder}.py")

        if os.path.isfile(real_py):
            # Re-export from the real module
            with open(py_file, "w") as fh:
                fh.write(
                    f"from fde_component.fde_component.doctypes.{folder}.{folder} "  # noqa: E501
                    f"import *  # noqa: F401,F403\n"
                    f"from fde_component.fde_component.doctypes.{folder}.{folder} "  # noqa: E501
                    f"import {folder}  # noqa: F401\n"
                )
            print(f"SHIM  {doc_name:25s}  scrub={s:20s}  folder={folder}  -> {py_file}")
        else:
            # No real .py — fall back to a plain Document subclass
            pascal = pascal_case(folder)
            with open(py_file, "w") as fh:
                fh.write(
                    f"import frappe\n"
                    f"from frappe.model.document import Document\n"
                    f"\n"
                    f"\n"
                    f"class {pascal}(Document):\n"
                    f"    pass\n"
                )
            print(
                f"NO REAL PY, plain Document shim: {doc_name:25s}  "
                f"scrub={s:20s}  folder={folder}  -> {py_file}"
            )


if __name__ == "__main__":
    if len(sys.argv) != 2:
        print(f"Usage: {sys.argv[0]} <pkg_root>", file=sys.stderr)
        sys.exit(1)
    make_shims(sys.argv[1])
