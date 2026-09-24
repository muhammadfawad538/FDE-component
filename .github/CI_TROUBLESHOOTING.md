# Frappe CI Setup & Troubleshooting

## Overview

This document records the GitHub Actions CI setup for the `fde_component` Frappe application, including the environment requirements, configuration decisions, errors encountered, root causes, fixes, and the final working setup.

The goal is to make the CI process reproducible for other developers and prevent previously resolved issues from recurring.

---

## Final Known-Good Configuration

| Component     | Version / Configuration                    |
| ------------- | ------------------------------------------ |
| Python        | 3.14 (GitHub Actions `setup-python@v5`)    |
| Frappe        | 16.35.0                                    |
| Frappe commit | `012667b9c4e7f66d5e1ff5858d2e922331d4300a` |
| Bench         | 5.31.0                                     |
| Node.js       | 24.11.0                                    |
| MariaDB       | GitHub Actions host/service, root password `test` |
| Redis         | Redis 7 service                            |
| OS            | Ubuntu GitHub Actions runner               |

---

## Application Structure

```text
fde_component/
├── setup.py
├── patches.txt
├── fixtures/
├── tests/
└── fde_component/
    ├── __init__.py
    ├── hooks.py
    ├── api.py
    ├── doctypes/
    └── jobs/
```

Key points:

- The Python package is `fde_component/fde_component/`.
- There is **no** `fde_component/__init__.py` at the outer app root.
- `hooks.py` lives inside the declared Python package: `fde_component/fde_component/hooks.py`.
- `tests/` stays at the application root (`fde_component/tests/`), not inside the Python package.

---

## Python Package Configuration

`setup.py` declares:

```python
packages=[
    "fde_component",
    "fde_component.jobs",
    "fde_component.doctypes",
],
```

`fde_component.tests` is **not** declared because `tests/` is not inside the `fde_component/fde_component/` package directory.

---

## Bench Environment

`bench init` creates a virtual environment at `fde_bench/env/`. The Bench Python interpreter is:

```
fde_bench/env/bin/python
```

Frappe and `fde_component` must be installed into this virtual environment, not the GitHub runner's system Python.

```bash
fde_bench/env/bin/python -m pip install -q -e fde_bench/apps/fde_component
```

Using the host `pip` (without `-m pip` inside the venv) installs into the wrong environment and causes `ModuleNotFoundError`.

---

## Application Registration

Frappe requires the application to be registered in `fde_bench/sites/apps.txt`. The CI workflow writes it deterministically:

```bash
printf "frappe\nfde_component\n" > sites/apps.txt
```

This produces:

```
frappe
fde_component
```

with a newline after each entry.

Using `echo "fde_component" >> sites/apps.txt` is unreliable because the initial `apps.txt` created by `bench new-site` does not always end with a newline, which can produce `frappefde_component` on a single line.

---

## Python 3.14 Compatibility Patch

Frappe 16.35.0 has a Python 3.14 compatibility issue in `frappe/utils/__init__.py`. The CI workflow applies a guarded patch after installing Frappe:

```python
# Before:
if key in v:

# After:
if hasattr(v, "__contains__") and key in v:
```

The patch is guarded: it fails clearly if the expected line is absent or changed, so it won't silently corrupt an unexpected Frappe version.

---

## MariaDB Configuration

The root database password is set to `test`. The CI workflow configures MariaDB root authentication before creating the site:

```bash
sudo mysql -u root -e "ALTER USER 'root'@'localhost' IDENTIFIED BY 'test'; FLUSH PRIVILEGES;"
```

The site is created with:

```bash
bench new-site test.localhost \
  --mariadb-root-password test \
  --admin-password admin \
  --force
```

---

## Redis

Redis runs as a GitHub Actions service (`redis:7-alpine`). During `bench build`, Frappe may print warnings about not being able to connect to `redis_cache`. These warnings did not prevent initialization or installation in CI.

---

## Click Dependency Warning

The CI reports:

```
frappe-bench 5.31.0 requires click~=8.2.0, but you have click 8.4.2
```

This is a known dependency metadata conflict between `frappe-bench 5.31.0` and `Frappe 16.35.0`. The CI job succeeds with Click 8.4.2. Do not add `pip check` as a failure gate solely because of this warning.

---

## Problems Encountered

### Error 1 — Incorrect Python Version

**Error:** Frappe 16.35.0 requires Python 3.14.x. The initial CI used an incompatible Python version.

**Fix:** Use `setup-python@v5` with `python-version: "3.14"`.

---

### Error 2 — Node.js Missing

**Error:** Frappe 16 requires a modern Node.js version.

**Fix:** Use Node.js 24 in CI.

---

### Error 3 — `ModuleNotFoundError: No module named 'fde_component'`

**Root cause:** The application was installed into the GitHub runner's host Python environment using `pip install -e ...`. Frappe runs inside the Bench virtual environment (`fde_bench/env/`), which could not see the host Python's site-packages.

**Fix:** Install using the Bench Python:

```bash
fde_bench/env/bin/python -m pip install -q -e fde_bench/apps/fde_component
```

---

### Error 4 — `ModuleNotFoundError: No module named 'fde_component.hooks'`

**Root cause:** `setup.py` declared `"fde_component"` as the package, which resolved to `fde_component/fde_component/`. Python looked for `fde_component/fde_component/hooks.py`, but `hooks.py` was at `fde_component/hooks.py` (the app root). The file was in the wrong directory relative to the declared package.

**Fix:** Move `hooks.py` into the declared package directory:

```
fde_component/fde_component/hooks.py
```

Also remove the outer `fde_component/__init__.py` that had been added as a workaround, since it can interfere with Python package resolution.

---

### Error 5 — `App fde_component not in apps.txt`

**Root cause:** `fde_component` was copied into `fde_bench/apps/` but never registered in `fde_bench/sites/apps.txt`. Frappe reads this file to know which apps are available.

**Fix:** Write the file deterministically before `bench install-app`:

```bash
printf "frappe\nfde_component\n" > sites/apps.txt
```

---

### Error 6 — `frappefde_component` in `apps.txt`

**Root cause:** The original `apps.txt` created by `bench new-site` did not end with a newline. Appending with `echo "fde_component" >> sites/apps.txt` produced `frappefde_component` on a single line.

**Fix:** Use `printf "frappe\nfde_component\n" > sites/apps.txt` to write both lines with explicit newlines, overwriting the file rather than appending.

---

## Final Installation Flow

The working sequence in the CI workflow is:

```
GitHub Actions checkout
        ↓
Python 3.14.7 + Node 24.11.0
        ↓
Install frappe-bench==5.31.0 (host pip)
        ↓
bench init → creates fde_bench/ with env/ venv
        ↓
Clone Frappe, checkout exact SHA, install into host pip
        ↓
Apply Python 3.14 compatibility patch
        ↓
cp -r fde_component → fde_bench/apps/fde_component
        ↓
Install fde_component into Bench venv
        ↓
bench new-site test.localhost
        ↓
printf "frappe\nfde_component\n" > sites/apps.txt
        ↓
bench install-app fde_component
        ↓
bench run-tests --app fde_component
```

---

## Important Rules for Future Changes

1. Do not change Python away from 3.14 without verifying Frappe compatibility.
2. Do not install the app using the host `pip`. Use `fde_bench/env/bin/python -m pip install`.
3. Keep `hooks.py` inside the declared Python package (`fde_component/fde_component/hooks.py`).
4. Do not add an `__init__.py` at the outer app root (`fde_component/__init__.py`).
5. Keep `sites/apps.txt` correctly formatted with explicit newlines.
6. Do not change the Click version merely because of the dependency warning.
7. Do not treat Redis build warnings as failures unless they cause an actual install/test failure.
8. Keep the Frappe commit pinned unless there is a deliberate upgrade.
9. When debugging a future failure, identify the first actual error and change one thing at a time.
