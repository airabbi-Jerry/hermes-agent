"""Per-job state budget: the run-scoped tool-output cap reaches read_file only for budgeted jobs."""

import os
from pathlib import Path
from types import SimpleNamespace

import pytest

from cron.scheduler import _apply_cron_state_budget
from tools import tool_output_limits as tol


@pytest.fixture
def configured_max_bytes():
    """Config ``tool_output.max_bytes`` stricter than the budget-derived cap (the live setup)."""
    Path(os.environ["HERMES_HOME"], "config.yaml").write_text("tool_output:\n  max_bytes: 8000\n")
    tol._reset_tool_output_limits_cache()
    yield 8_000
    tol._reset_tool_output_limits_cache()


def _agent():
    comp = SimpleNamespace(protect_last_n=20, context_length=1_000_000,
                           _apply_threshold_tokens_cap=lambda: None)
    return SimpleNamespace(context_compressor=comp, max_iterations=60)


def test_budget_sets_run_cap_no_looser_than_config_and_unbudgeted_job_sets_none(configured_max_bytes):
    applied = _apply_cron_state_budget(_agent(), {"state_budget_tokens": 32_000}, "j1")
    try:
        assert tol.get_run_output_cap() == min(int(32_000 * 0.5), configured_max_bytes)
        assert tol.get_max_bytes() == configured_max_bytes
    finally:
        tol.reset_max_bytes_override(applied["_tool_output_token"])

    assert _apply_cron_state_budget(_agent(), {}, "j2") is None
    assert tol.get_run_output_cap() is None
    assert tol.get_max_bytes() == configured_max_bytes
