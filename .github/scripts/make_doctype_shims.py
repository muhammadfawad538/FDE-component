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

Also handles nested DocType folders (e.g. doctypes/sync_job/sync_job_checkpoint/)
by creating a top-level symlink in the CI copy, since Frappe's install-app
only discovers DocTypes at the top level of doctypes/.

Runs against the CI copy only (never touches the repo).
"""

import ast
import json
import os
import re
import sys


def scrub(name: str) -> str:
    return re.sub(r"[^a-z0-9]", "", name.lower())


def pascal_case(snake: str) -> str:
    """sync_job -> SyncJob, sync_job_dedup -> SyncJobDedup"""
    return "".join(w.capitalize() for w in snake.split("_"))


def module_has_class(py_path: str, class_name: str) -> bool:
    """Return True if the Python file at py_path defines a class named class_name."""
    try:
        with open(py_path) as fh:
            tree = ast.parse(fh.read())
        return any(
            isinstance(node, ast.ClassDef) and node.name == class_name
            for node in ast.walk(tree)
        )
    except Exception:
        return False


def inject_class_if_missing(py_path: str, class_name: str) -> bool:
    """
    If py_path does not define a class named class_name, append a
    CI-injected class definition to the file (CI copy only, never the repo).
    Returns True if injection was performed.
    """
    if module_has_class(py_path, class_name):
        return False

    injection = (
        f"\n"
        f"\n"
        f"# --- CI-injected: hooks.override_doctype_class references this class ---\n"
        f"from frappe.model.document import Document as _Doc\n"
        f"\n"
        f"\n"
        f"class {class_name}(_Doc):\n"
        f"    pass\n"
    )
    with open(py_path, "a") as fh:
        fh.write(injection)

    print(f"CI-INJECTED class {class_name} into {py_path}")
    return True


def find_nested_doctypes(doctypes_dir: str) -> list[dict]:
    """
    Recursively scan <doctypes_dir> for DocType folders that are NOT
    at the top level. Returns a list of dicts with keys:
        folder, folder_path, json_path, doc_name
    """
    results = []
    for root, dirs, files in os.walk(doctypes_dir):
        # Skip the top-level doctypes/ directory itself
        if root == doctypes_dir:
            continue
        # Only consider direct children of doctypes/ as top-level
        rel = os.path.relpath(root, doctypes_dir)
        if os.sep in rel or "/" in rel:
            # This is nested (e.g. sync_job/sync_job_checkpoint)
            folder = os.path.basename(root)
            json_path = os.path.join(root, f"{folder}.json")
            if os.path.isfile(json_path):
                try:
                    with open(json_path) as fh:
                        data = json.load(fh)
                    if data.get("doctype") == "DocType":
                        results.append(
                            {
                                "folder": folder,
                                "folder_path": root,
                                "json_path": json_path,
                                "doc_name": data.get("name", folder),
                            }
                        )
                except Exception:
                    pass
    return results


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

    # ── Step 1: find nested DocTypes and create top-level symlinks ──────────
    nested = find_nested_doctypes(DOCTYPES_DIR)
    for item in nested:
        folder = item["folder"]
        folder_path = item["folder_path"]
        # Compute relative path from DOCTYPES_DIR to the nested folder
        rel_target = os.path.relpath(folder_path, DOCTYPES_DIR)
        link_path = os.path.join(DOCTYPES_DIR, folder)
        if os.path.lexists(link_path):
            continue
        os.symlink(rel_target, link_path)
        # Ensure __init__.py in the linked folder (CI copy only)
        open(os.path.join(folder_path, "__init__.py"), "a").close()
        print(f"NESTED DOCTYPE linked: {folder} -> {rel_target}")

    # ── Step 2: process all DocTypes (top-level + newly linked) ────────────
    found = []
    for folder in sorted(os.listdir(DOCTYPES_DIR)):
        folder_path = os.path.join(DOCTYPES_DIR, folder)
        if not os.path.isdir(folder_path):
            continue

        json_path = os.path.join(folder_path, f"{folder}.json")
        if not os.path.isfile(json_path):
            continue

        # Ensure __init__.py in the folder
        open(os.path.join(folder_path, "__init__.py"), "a").close()

        with open(json_path) as fh:
            data = json.load(fh)
        doc_name = data.get("name", "")
        found.append(f"{folder} ({doc_name})")

        # Symlink DOCTYPE_DIR/<folder> -> <pkg_root>/doctypes/<folder>
        # Relative from DOCTYPE_DIR: ../<folder> or ../../<folder>
        rel_to_doctype = os.path.relpath(folder_path, DOCTYPE_DIR)
        symlink_path = os.path.join(DOCTYPE_DIR, folder)
        if os.path.lexists(symlink_path):
            os.unlink(symlink_path)
        os.symlink(rel_to_doctype, symlink_path)

        # Build shim path: DOCTYPE_DIR/<scrub>/
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
            # Workaround: if override_doctype_class references a class
            # that doesn't exist in the real module, inject it into the
            # CI copy so the shim can re-export it.
            inject_class_if_missing(real_py, doc_name)

            # Re-export every public name from the real module
            with open(py_file, "w") as fh:
                fh.write(
                    f"import importlib as _il\n"
                    f"_real = _il.import_module(\n"
                    f"    \"fde_component.fde_component.doctypes.{folder}.{folder}\"\n"
                    f")\n"
                    f"for _n in dir(_real):\n"
                    f"    if not _n.startswith(\"__\"):\n"
                    f"        globals()[_n] = getattr(_real, _n)\n"
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

    print(f"\nDocTypes found ({len(found)}): {', '.join(found)}")


if __name__ == "__main__":
    if len(sys.argv) != 2:
        print(f"Usage: {sys.argv[0]} <pkg_root>", file=sys.stderr)
        sys.exit(1)
    make_shims(sys.argv[1])
