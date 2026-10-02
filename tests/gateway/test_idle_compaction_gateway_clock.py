"""Idle compaction on gateway sessions: the idle clock must survive the per-turn reset.

The gateway resets ``_last_activity_ts`` at the start of every cached turn and a fresh agent starts
it at construction, so ``_idle_compaction`` measured a ~0s gap on every Telegram turn and never
fired (t_d63974bf). The gateway now hands the previous activity stamp over via
``_idle_compact_reference_ts``.
"""

from __future__ import annotations

import time
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

from hermes_state import SessionDB

from agent.turn_context_compaction import _idle_reference_ts
from gateway.run import GatewayRunner
from gateway.run_turn_runner import TurnRunner
from tests.agent.test_idle_compaction_lock_and_guards import _history, _prep_idle_agent, _run_prologue


def test_cached_turn_reset_hands_previous_activity_to_idle_clock():
    agent = MagicMock()
    agent._last_activity_ts = time.time() - 3 * 3600
    before = agent._last_activity_ts

    GatewayRunner._init_cached_agent_for_turn(agent, interrupt_depth=0)

    assert agent._idle_compact_reference_ts == before
    assert agent._last_activity_ts > before + 3 * 3600 - 5


def test_interrupt_recursive_turn_does_not_rearm_the_reference():
    agent = SimpleNamespace(_last_activity_ts=123.0, _api_call_count=5)

    GatewayRunner._init_cached_agent_for_turn(agent, interrupt_depth=1)

    assert not hasattr(agent, "_idle_compact_reference_ts")


def test_reference_is_one_shot_and_falls_back_to_activity_clock():
    agent = SimpleNamespace(_idle_compact_reference_ts=1_000.0, _last_activity_ts=2_000.0)
    assert _idle_reference_ts(agent) == 1_000.0
    assert agent._idle_compact_reference_ts is None
    assert _idle_reference_ts(agent) == 2_000.0
    # Non-numeric references (MagicMock doubles) never steer the clock.
    assert _idle_reference_ts(SimpleNamespace(_idle_compact_reference_ts=MagicMock(), _last_activity_ts=5.0)) == 5.0


def test_idle_compaction_fires_after_gateway_reset(tmp_path: Path):
    """Real AIAgent: activity clock freshly reset (gateway turn start) but the handed-over
    reference is 3h old → idle compaction runs."""
    db = SessionDB(db_path=tmp_path / "state.db")
    sid = "GW_IDLE"
    db.create_session(sid, source="telegram")
    agent = _prep_idle_agent(db, sid, idle_after=7200, idle_gap=3 * 3600)
    GatewayRunner._init_cached_agent_for_turn(agent, interrupt_depth=0)

    _run_prologue(agent, _history())

    agent.context_compressor.compress.assert_called_once()


def test_idle_compaction_still_skips_recent_activity(tmp_path: Path):
    db = SessionDB(db_path=tmp_path / "state.db")
    sid = "GW_BUSY"
    db.create_session(sid, source="telegram")
    agent = _prep_idle_agent(db, sid, idle_after=7200, idle_gap=600)
    GatewayRunner._init_cached_agent_for_turn(agent, interrupt_depth=0)

    _run_prologue(agent, _history())

    agent.context_compressor.compress.assert_not_called()


def test_fresh_agent_reference_reads_durable_last_activity(tmp_path: Path):
    db = SessionDB(db_path=tmp_path / "state.db")
    sid = "GW_FRESH"
    db.create_session(sid, source="telegram")
    stamp = time.time() - 4 * 3600
    db.touch_session_activity(sid, stamp)
    runner = TurnRunner.__new__(TurnRunner)
    runner._ctx = SimpleNamespace(session_id=sid)
    runner._runner = SimpleNamespace(_session_db=SimpleNamespace(_db=db))

    assert abs(runner._durable_last_activity_ts() - stamp) < 1e-3
    runner._ctx = SimpleNamespace(session_id="missing")
    assert runner._durable_last_activity_ts() is None


def test_idle_key_busts_the_gateway_agent_cache():
    assert ("compression", "idle_compact_after_seconds") in GatewayRunner._CACHE_BUSTING_CONFIG_KEYS
