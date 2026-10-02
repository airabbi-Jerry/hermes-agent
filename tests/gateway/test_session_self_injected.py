"""``HERMES_SESSION_SELF_INJECTED`` tells tools whether the current gateway turn is a real inbound
message or one the gateway injected into the same chat (internal notices, wakes, heartbeats).

Injected turns reuse the sender's identity vars (platform, user id, even a stale message id), so a
tool that may act only on the sender's live instruction needs this declaration to tell them apart.
"""

import pytest

from gateway.session_context import get_session_env, set_session_vars, clear_session_vars
from tests.gateway.test_internal_notification_marker import SESSION_KEY, _bootstrap, _event, _source


@pytest.mark.asyncio
@pytest.mark.parametrize("internal, expected", [(True, "1"), (False, "0")])
async def test_turn_declares_whether_it_was_self_injected(monkeypatch, tmp_path, internal, expected):
    runner = _bootstrap(monkeypatch, tmp_path)
    del runner._set_session_env  # the real binder, not the harness stub
    seen = {}

    async def _run_agent(*_args, **_kwargs):
        seen["self_injected"] = get_session_env("HERMES_SESSION_SELF_INJECTED")
        seen["user_id"] = get_session_env("HERMES_SESSION_USER_ID")
        return {"final_response": "ack", "messages": [], "tools": [], "history_offset": 0, "last_prompt_tokens": 0}

    runner._run_agent = _run_agent
    await runner._handle_message_with_agent(_event(internal=internal), _source(), SESSION_KEY, 1)

    # Same sender identity either way; only the declaration differs.
    assert seen == {"self_injected": expected, "user_id": _source().user_id}


def test_a_new_binding_starts_undeclared(monkeypatch):
    """A later bind (e.g. a plugin command scope) must not inherit the previous turn's "0"."""
    monkeypatch.setenv("HERMES_SESSION_SELF_INJECTED", "0")
    from gateway.session_context import declare_self_injected

    tokens = set_session_vars(platform="telegram", user_id="12345")
    declare_self_injected(False)
    clear_session_vars(tokens)
    tokens = set_session_vars(platform="telegram", user_id="12345")
    try:
        assert get_session_env("HERMES_SESSION_SELF_INJECTED") == ""
    finally:
        clear_session_vars(tokens)
