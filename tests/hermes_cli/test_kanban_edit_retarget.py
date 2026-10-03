"""`kanban edit --workspace / --completion-contract` — fix a mis-specified card in place.

Before this, a card created with the wrong workspace (scratch, no path, the main checkout) or
contract could only be recreated + linked + archived. The edit reuses create's flag grammar,
refuses to move a workspace out from under a worker, and records old -> new for audit.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import pytest

from hermes_cli import kanban as kc
from hermes_cli import kanban_db as kb
from hermes_cli import kanban_db_connect as kbc


@pytest.fixture
def kanban_home(tmp_path, monkeypatch):
    home = tmp_path / ".hermes"
    home.mkdir()
    monkeypatch.setenv("HERMES_HOME", str(home))
    monkeypatch.setenv("HERMES_KANBAN_HOME", str(home))
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    kb.init_db()
    return home


def _edit(*argv):
    root = argparse.ArgumentParser(prog="hermes")
    kc.build_parser(root.add_subparsers())
    return kc._cmd_edit(root.parse_args(["kanban", "edit", *argv]))


def _task(tid):
    with kbc.connect_closing() as conn:
        return kb.get_task(conn, tid)


def _edited_payloads(tid):
    with kbc.connect_closing() as conn:
        rows = conn.execute(
            "SELECT payload FROM task_events WHERE task_id = ? AND kind = 'edited' ORDER BY id", (tid,),
        ).fetchall()
    return [json.loads(r["payload"]) for r in rows]


def _create(**kw):
    with kbc.connect_closing() as conn:
        return kb.create_task(conn, title="card", body="spec", assignee="worker", **kw)


def test_edit_repoints_workspace_and_contract_with_audit_event(kanban_home, tmp_path):
    tid = _create(workspace_kind="worktree", branch_name="wt/old")
    target = tmp_path / "checkout"
    target.mkdir()

    assert _edit(tid, "--workspace", f"dir:{target}", "--completion-contract", "acme/repo") == 0

    task = _task(tid)
    assert (task.workspace_kind, task.workspace_path, task.branch_name) == ("dir", str(target), None)
    assert task.completion_contract == "acme/repo"
    [event] = _edited_payloads(tid)
    assert event["changes"]["workspace_kind"] == {"from": "worktree", "to": "dir"}
    assert event["changes"]["workspace_path"] == {"from": None, "to": str(target)}
    assert event["changes"]["branch_name"] == {"from": "wt/old", "to": None}
    assert event["changes"]["completion_contract"] == {"from": "local-only", "to": "acme/repo"}


@pytest.mark.parametrize("workspace", ["dir:relative/path", "dir:{missing}", "scratchy"])
def test_edit_rejects_unusable_workspace_without_writing(kanban_home, tmp_path, workspace):
    tid = _create()
    before = _task(tid)

    rc = _edit(tid, "--workspace", workspace.format(missing=tmp_path / "nope"),
               "--completion-contract", "acme/repo")

    assert rc != 0
    after = _task(tid)
    assert (after.workspace_kind, after.workspace_path, after.completion_contract) == (
        before.workspace_kind, before.workspace_path, before.completion_contract)
    assert _edited_payloads(tid) == []


def test_edit_refuses_workspace_move_on_claimed_task(kanban_home, tmp_path):
    tid = _create(workspace_kind="dir", workspace_path=str(tmp_path))
    with kbc.connect_closing() as conn:
        assert kb.claim_task(conn, tid) is not None
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()

    assert _edit(tid, "--workspace", f"dir:{elsewhere}") == 1
    assert _task(tid).workspace_path == str(tmp_path)

    # The contract is read at completion time, so it stays editable mid-run.
    assert _edit(tid, "--completion-contract", "acme/repo") == 0
    assert _task(tid).completion_contract == "acme/repo"
