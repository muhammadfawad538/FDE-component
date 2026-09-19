"""
fde_component.tests.test_api
==============================
API endpoint contract tests.

Tests verify the public API methods exist and have the expected signatures.
"""

from __future__ import annotations

import pytest

import fde_component.api as api_module


class TestAPIMethods:

    def test_redrive_from_dlq_exists(self):
        assert hasattr(api_module, "redrive_from_dlq")
        assert callable(api_module.redrive_from_dlq)

    def test_resume_job_exists(self):
        assert hasattr(api_module, "resume_job")
        assert callable(api_module.resume_job)

    def test_redrive_from_dlq_signature(self):
        import inspect
        sig = inspect.signature(api_module.redrive_from_dlq)
        params = list(sig.parameters.keys())
        assert "job_name" in params

    def test_resume_job_signature(self):
        import inspect
        sig = inspect.signature(api_module.resume_job)
        params = list(sig.parameters.keys())
        assert "job_name" in params
