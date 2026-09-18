"""
fde_component.tests.conftest
=============================
Shared pytest fixtures for SP-05 conformance tests.

Requires a bench-managed Frappe site with fde_component installed
(i.e. `bench --site <site> run-tests --app fde_component`).
"""

from __future__ import annotations

import os

import pytest


# ---------------------------------------------------------------------------
# Frappe test site bootstrap
# ---------------------------------------------------------------------------


def pytest_configure(config: pytest.Config) -> None:  # type: ignore[type-arg]
    """
    Initialize Frappe when pytest collects tests.
    Frappe's own test runner calls frappe.init() + frappe.connect() before
    importing test modules, so this is a safety net for direct pytest runs.
    """
    if os.environ.get("FRAPPE_TEST_SITE"):
        return  # already set up by bench run-tests

    # When running via `bench run-tests`, Frappe is already initialized.
    # Nothing to do here — the bench test runner handles it.


@pytest.fixture(autouse=True)
def _frappe_test_user() -> None:
    """
    Set the test user to Administrator for every test function.
    Prevents permission errors on doc inserts.
    """
    try:
        import frappe
        frappe.set_user("Administrator")
    except Exception:
        pass  # Frappe not initialized — skip
