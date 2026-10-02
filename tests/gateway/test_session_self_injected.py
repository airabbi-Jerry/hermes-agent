"""The gateway declares per turn whether it wrote the turn itself (``session_self_injected()``).

Injected turns — internal notices, wakes, heartbeats, goal/retry prompts, queued follow-ups —
run on the sender's identity vars (platform, user id, even a stale message id), so a tool that may
act only on the sender's live instruction needs this declaration to tell them apart.
"""

from types import SimpleNamespace

import pytest

from gateway.config import Platform
from gateway.platforms.event import MessageEvent
from gateway.session import SessionSource
from gateway.session_context import (
    clear_session_vars, declare_self_injected, get_session_env, session_self_injected, set_session_vars,
)
from tests.gateway.test_internal_notification_marker import SESSION_KEY, _bootstrap, _source

_AGENT_RESULT = {"final_response": "ack", "messages": [], "tools": [], "history_offset": 0, "last_prompt_tokens": 0}


def _msg(*, internal=False, message_id="4711", user_id="12345"):
    source = SessionSource(platform=Platform.TELEGRAM, chat_id="-1001", chat_type="group", user_id=user_id,
                           message_id="4700")  # a stale anchor, like a restored or /goal command source
    return MessageEvent(text="X 完成", source=source, message_id=message_id, internal=internal)


@pytest.mark.asyncio
@pytest.mark.parametrize("event, expected", [
    (_msg(), "0"),                                  # the sender's own platform message
    (_msg(internal=True), "1"),                     # background / delegation / kanban notice, wake
    (_msg(message_id=None), "1"),                   # /goal or /retry prompt written by the gateway
])
async def test_turn_declares_whether_the_gateway_wrote_it(monkeypatch, tmp_path, event, expected):
    runner = _bootstrap(monkeypatch, tmp_path)
    del runner._set_session_env  # the real binder, not the harness stub
    seen = {}

    async def _run_agent(*_args, **_kwargs):
        seen.update(self_injected=session_self_injected(), user_id=get_session_env("HERMES_SESSION_USER_ID"))
        return _AGENT_RESULT

    runner._run_agent = _run_agent
    await runner._handle_message_with_agent(event, _source(), SESSION_KEY, 1)

    # Same sender identity either way; only the declaration differs.
    assert seen == {"self_injected": expected, "user_id": "12345"}


@pytest.mark.asyncio
@pytest.mark.parametrize("pending, previous_sender, expected", [
    (_msg(message_id="4712"), "12345", "0"),                 # the sender's next message, queued while busy
    (_msg(internal=True), "12345", "1"),                     # a notice queued behind the live turn
    (_msg(message_id="4712", user_id="999"), "12345", "1"),  # someone else in a shared session
    (_msg(message_id="4713", user_id="999"), "999", "1"),    # ...and their next one, chained behind it
])
async def test_queued_followup_declares_its_own_origin(monkeypatch, tmp_path, pending, previous_sender, expected):
    """Follow-ups run in-band on the first turn's bound vars (bound to 12345); they must not inherit its "0"."""
    runner = _bootstrap(monkeypatch, tmp_path)
    seen = {}

    async def _run_agent(*_args, **_kwargs):
        seen["self_injected"] = session_self_injected()
        return _AGENT_RESULT

    async def _text(**kwargs):
        return kwargs["event"].text

    async def _noop(*_a, **_kw):
        return None

    runner._run_agent = _run_agent
    runner._prepare_profile_scoped_inbound_message_text = _text
    runner._refresh_agent_cache_message_count = _noop
    runner._session_key_for_source = lambda _source: SESSION_KEY
    runner._is_goal_continuation_event = lambda _event: False
    runner._delivery_adapter_for = runner._intake_adapter_for = lambda _source: None
    previous = _msg(user_id=previous_sender)  # the turn this follow-up is chained behind
    turn_ctx = SimpleNamespace(source=previous.source, session_id="sess", session_key=SESSION_KEY, run_generation=1,
                               _interrupt_depth=0, history=[], _status_thread_metadata=None, result_holder=[None],
                               context_prompt="")
    tokens = set_session_vars(platform="telegram", user_id="12345", message_id="4711")
    declare_self_injected(False)  # the live outer turn
    try:
        await runner._run_agent_queued_followup(turn_ctx, None, pending.text, pending, "ack",
                                                {"interrupted": True, "messages": []}, None)
    finally:
        clear_session_vars(tokens)
    assert seen == {"self_injected": expected}


def test_declaration_is_request_local(monkeypatch):
    """No env fallback (a process started from a turn's terminal inherits nothing usable) and every
    new bind starts undeclared, so a later bind cannot inherit the previous turn's "0"."""
    monkeypatch.setenv("HERMES_SESSION_SELF_INJECTED", "0")
    tokens = set_session_vars(platform="telegram", user_id="12345")
    declare_self_injected(False)
    clear_session_vars(tokens)
    tokens = set_session_vars(platform="telegram", user_id="12345")
    try:
        assert session_self_injected() == ""
    finally:
        clear_session_vars(tokens)
