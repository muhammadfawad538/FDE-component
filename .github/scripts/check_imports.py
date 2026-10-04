"""
CI-only: pre-install import checks.

Tries importing each DocType module via both the real path
(doctypes/<folder>/<folder>) and the scrubbed shim path
(doctype/<scrub>/<scrub>), plus hooks and API modules.

Usage:
    check_imports.py [pkg_root]

pkg_root defaults to $BENCH_DIR/apps/fde_component/fde_component
or the current directory if BENCH_DIR is not set.

Always exits 0 — diagnostic only.
"""

import importlib
import json
import os
import re
import sys


def scrub(name: str) -> str:
    return re.sub(r"[^a-z0-9]", "", name.lower())


def try_import(module_path: str) -> None:
    try:
        importlib.import_module(module_path)
        print(f"OK    {module_path}")
    except Exception as exc:
        print(f"FAIL  {module_path}: {exc}")


def main() -> None:
    # Resolve package root from argv or env
    if len(sys.argv) > 1:
        pkg_root = sys.argv[1]
    else:
        bench_dir = os.environ.get("BENCH_DIR", ".")
        pkg_root = os.path.join(bench_dir, "apps", "fde_component", "fde_component")

    if not os.path.isdir(pkg_root):
        print(f"PKG_ROOT not found: {pkg_root}")
        return

    # Ensure the package is importable
    apps_dir = os.path.dirname(pkg_root)
    if apps_dir not in sys.path:
        sys.path.insert(0, apps_dir)

    doctypes_dir = os.path.join(pkg_root, "doctypes")
    if not os.path.isdir(doctypes_dir):
        print(f"doctypes/ not found in {pkg_root}")
        return

    # Collect all DocType folders: top-level + nested (via symlinks or real dirs)
    folders = []
    for root, dirs, files in os.walk(doctypes_dir):
        if root == doctypes_dir:
            continue
        rel = os.path.relpath(root, doctypes_dir)
        if os.sep in rel or "/" in rel:
            folder = os.path.basename(root)
            if os.path.isfile(os.path.join(root, f"{folder}.json")):
                folders.append((root, folder))

    for root, folder in sorted(folders):
        json_path = os.path.join(root, f"{folder}.json")
        with open(json_path) as fh:
            data = json.load(fh)
        s = scrub(data.get("name", ""))

        real_py = os.path.join(root, f"{folder}.py")

        # Real path — skip with a note if no real .py exists
        if os.path.isfile(real_py):
            try_import(f"fde_component.fde_component.doctypes.{folder}.{folder}")
        else:
            print(f"SKIP (no real .py)  fde_component.fde_component.doctypes.{folder}.{folder}")

        # Shim path (only if scrub differs from folder)
        if s != folder:
            try_import(f"fde_component.fde_component.doctype.{s}.{s}")

    # Hooks and API modules
    try_import("fde_component.jobs.scheduler")
    try_import("fde_component.api")


if __name__ == "__main__":
    main()
