"""
CI-only: pre-install import checks.

Tries importing each DocType module via both the real path
(doctypes/<folder>/<folder>) and the scrubbed shim path
(doctype/<scrub>/<scrub>), plus hooks and API modules.

Run from inside fde_bench/ after pip install -e but before
bench install-app.
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
    pkg = "fde_component/fde_component"

    for folder in sorted(os.listdir(os.path.join(pkg, "doctypes"))):
        json_path = os.path.join(pkg, "doctypes", folder, f"{folder}.json")
        if not os.path.isfile(json_path):
            continue

        with open(json_path) as fh:
            data = json.load(fh)
        s = scrub(data.get("name", ""))

        # Real path
        try_import(f"fde_component.fde_component.doctypes.{folder}.{folder}")
        # Shim path (only if scrub differs from folder)
        if s != folder:
            try_import(f"fde_component.fde_component.doctype.{s}.{s}")

    # Hooks and API modules
    try_import("fde_component.jobs.scheduler")
    try_import("fde_component.api")


if __name__ == "__main__":
    main()
