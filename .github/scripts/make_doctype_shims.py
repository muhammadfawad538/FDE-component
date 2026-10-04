"""
CI-only: build doctype shim packages so Frappe's load_doctype_module()
can resolve scrub(DocType.name) to the correct folder/file.

Frappe imports fde_component.fde_component.doctype.<scrub>.<scrub>, so
the shim must live under <pkg>/fde_component/doctype/, NOT <pkg>/doctype/.

Example: DocType "SyncJob" has folder sync_job/ but Frappe scrubs it
to "syncjob". This script creates:
    fde_component/fde_component/doctype/syncjob/__init__.py
    fde_component/fde_component/doctype/syncjob/syncjob.py

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
    MODULE_DIR = os.path.join(pkg_root, "fde_component")
    DOCTYPE_DIR = os.path.join(MODULE_DIR, "doctype")
    DOCTYPES_DIR = os.path.join(pkg_root, "doctypes")

    # If DOCTYPE_DIR is a symlink (from earlier workflow step), remove it
    if os.path.lexists(DOCTYPE_DIR) and os.path.islink(DOCTYPE_DIR):
        os.unlink(DOCTYPE_DIR)

    # Create DOCTYPE_DIR as a real directory
    os.makedirs(DOCTYPE_DIR, exist_ok=True)
    open(os.path.join(DOCTYPE_DIR, "__init__.py"), "a").close()

    # Ensure MODULE_DIR/__init__.py exists
    open(os.path.join(MODULE_DIR, "__init__.py"), "a").close()

    # Ensure MODULE_DIR/doctypes symlink exists (for hooks.py imports)
    doctypes_link = os.path.join(MODULE_DIR, "doctypes")
    if not os.path.lexists(doctypes_link):
        os.symlink("../doctypes", doctypes_link)

    # Ensure __init__.py in DOCTYPES_DIR
    open(os.path.join(DOCTYPES_DIR, "__init__.py"), "a").close()

    for folder in sorted(os.listdir(DOCTYPES_DIR)):
        folder_path = os.path.join(DOCTYPES_DIR, folder)
        if not os.path.isdir(folder_path):
            continue

        json_path = os.path.join(folder_path, f"{folder}.json")
        if not os.path.isfile(json_path):
            continue

        # Ensure __init__.py in the folder
        open(os.path.join(folder_path, "__init__.py"), "a").close()

        # Symlink DOCTYPE_DIR/<folder> -> <pkg_root>/doctypes/<folder>
        # Relative from DOCTYPE_DIR: ../../doctypes/<folder>
        symlink_path = os.path.join(DOCTYPE_DIR, folder)
        symlink_target = os.path.join("..", "..", "doctypes", folder)
        if os.path.lexists(symlink_path):
            os.unlink(symlink_path)
        os.symlink(symlink_target, symlink_path)

        # Build shim path: DOCTYPE_DIR/<scrub>/
        with open(json_path) as fh:
            data = json.load(fh)
        doc_name = data.get("name", "")
        s = scrub(doc_name)

        if s == folder:
            continue

        shim_dir = os.path.join(DOCTYPE_DIR, s)
        if os.path.lexists(shim_dir):
            continue

        os.makedirs(shim_dir, exist_ok=True)
        open(os.path.join(shim_dir, "__init__.py"), "a").close()

        py_file = os.path.join(shim_dir, f"{s}.py")
        real_py = os.path.join(folder_path, f"{folder}.py")

        if os.path.isfile(real_py):
            with open(py_file, "w") as fh:
                fh.write(
                    f"import importlib as _il\n"
                    f"_real = _il.import_module(\n"
                    f"    \"fde_component.fde_component.doctypes.{folder}.{folder}\"\n"
                    f")\n"
                    f"from fde_component.fde_component.doctypes.{folder}.{folder} "  # noqa: E501
                    f"import *  # noqa: F401,F403\n"
                    f"\n"
                    f"\n"
                    f"def __getattr__(name):\n"
                    f"    return getattr(_real, name)\n"
                )
            print(f"SHIM  {doc_name:25s}  scrub={s:20s}  folder={folder}  -> {py_file}")
        else:
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
