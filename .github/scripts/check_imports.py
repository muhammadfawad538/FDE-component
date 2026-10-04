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

    for folder in sorted(os.listdir(doctypes_dir)):
        json_path = os.path.join(doctypes_dir, folder, f"{folder}.json")
        if not os.path.isfile(json_path):
            continue

        with open(json_path) as fh:
            data = json.load(fh)
        s = scrub(data.get("name", ""))

        real_py = os.path.join(pkg_root, "doctypes", folder, f"{folder}.py")

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
