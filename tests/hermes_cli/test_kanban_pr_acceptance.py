"""PR acceptance invariants, using real SQLite and a local GitHub HTTP contract."""
import json
import os
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from hermes_cli import kanban_db as kb
from hermes_cli.kanban_db_connect import connect
from hermes_cli.kanban_pr_acceptance import collect_acceptance


@pytest.fixture
def github(tmp_path, monkeypatch):
    state = {"conclusion": "success", "head": "a" * 40, "reads": 0, "requests": []}

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            state["requests"].append(self.path)
            sha = state["head"]
            if self.path == "/graphql":
                value = {"data": {"repository": {"pullRequest": {
                    "headRefOid": sha, "baseRefName": "main", "state": "OPEN",
                    "baseRef": {"branchProtectionRule": None if state.get("unprotected") else {"requiredStatusChecks": [
                        {"context": "required", "app": {"databaseId": 1}}]}}}}}}
            elif "/rules/branches/" in self.path and state.get("rules_error"):
                status, body = state["rules_error"]
                self.send_response(status)
                self.end_headers()
                self.wfile.write((body if isinstance(body, str) else json.dumps(body)).encode())
                return
            elif "/rules/branches/" in self.path:
                value = [[]]
            elif "/check-runs" in self.path:
                run = {"id": 42, "name": state.get("name", "required"), "head_sha": sha,
                       "app": {"id": state.get("app", 1)}, "status": "in_progress" if state["conclusion"] == "pending" else "completed", "conclusion": state["conclusion"],
                       "html_url": "https://github.com/acme/repo/actions/runs/42"}
                if state.get("stale"):
                    run["head_sha"] = "b" * 40
                runs = [] if state.get("missing") else [run]
                value = [{"total_count": 100 + len(runs), "check_runs": [
                    {**run, "id": 1000 + i, "name": "optional", "conclusion": "skipped"}
                    for i in range(100)]}, {"total_count": 100 + len(runs), "check_runs": runs}]
                if state.get("race"):
                    state["race"]()
                if state.get("head_change"):
                    state["head"] = "b" * 40
            elif "/statuses" in self.path:
                value = [[]]
            elif "/pulls/" in self.path:
                value = {"head": {"sha": sha}, "base": {"ref": "main"}, "state": "open"}
            else:
                self.send_error(404)
                return
            self.send_response(200)
            self.end_headers()
            self.wfile.write(json.dumps(value).encode())

        def log_message(self, *args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    shim = tmp_path / "bin"
    shim.mkdir()
    gh = shim / "gh"
    # Like gh, an HTTP error prints the response body to stdout and exits non-zero.
    gh.write_text(f"#!{sys.executable}\nimport sys,urllib.error,urllib.request\n"
                  f"u='http://127.0.0.1:{server.server_port}/'+sys.argv[2]\n"
                  "try: print(urllib.request.urlopen(u).read().decode())\n"
                  "except urllib.error.HTTPError as e: print(e.read().decode()); sys.exit(1)\n")
    gh.chmod(0o755)
    monkeypatch.setenv("PATH", str(shim) + os.pathsep + os.environ["PATH"])
    monkeypatch.setenv("HERMES_HOME", str(tmp_path / "home"))
    kb.init_db()
    try:
        yield state
    finally:
        server.shutdown()
        server.server_close()
        thread.join()


@pytest.mark.linux_only
def test_pr_completion_requires_current_required_evidence(github):
    with connect() as conn:
        for conclusion in ("failure", "pending", "cancelled", "timed_out", "action_required", "neutral", "skipped", None, "success"):
            github.update(conclusion=conclusion, head="a" * 40)
            tid = kb.create_task(conn, title="Publish", completion_contract="acme/repo")
            ok = kb.complete_task(conn, tid, metadata={"published_pr": "https://github.com/acme/repo/pull/7"})
            assert ok is (conclusion == "success")
            task = kb.get_task(conn, tid)
            assert (task.status == "done") is ok
            receipts = [json.loads(r[0]) for r in conn.execute(
                "SELECT payload FROM task_events WHERE task_id=? AND kind='pr_acceptance'", (tid,))]
            assert receipts and receipts[-1]["head_sha"] == "a" * 40
            if not ok:
                assert task.status in {"running", "ready", "blocked", "review"}
                assert "retry" in receipts[-1]["recovery"]
                assert receipts[-1]["checks"][0]["id"] == 42
        for fault in ("missing", "stale", "head_change"):
            github.update(conclusion="success", head="a" * 40)
            github[fault] = True
            tid = kb.create_task(conn, title=fault, completion_contract="acme/repo")
            assert not kb.complete_task(conn, tid, metadata={"published_pr": "https://github.com/acme/repo/pull/7"})
            assert kb.get_task(conn, tid).status != "done"
            github.pop(fault)
        # Omission and a sibling repository cannot downgrade the stored declaration.
        tid = kb.create_task(conn, title="publish", completion_contract="acme/repo")
        assert not kb.complete_task(conn, tid, summary="local green")
        assert not kb.complete_task(conn, tid, metadata={"published_pr": "https://github.com/other/repo/pull/7"})
        before = len(github["requests"])
        local = kb.create_task(conn, title="local", completion_contract="local-only")
        assert kb.complete_task(conn, local, summary="https://github.com/acme/repo/pull/7 is background context")
        assert len(github["requests"]) == before


@pytest.mark.linux_only
def test_acceptance_receipts_and_terminal_write_share_run_ownership(github):
    with connect() as conn:
        for conclusion in ("success", "failure"):
            tid = kb.create_task(conn, title="race", completion_contract="acme/repo")
            owner = kb.claim_task(conn, tid)
            run_id = owner.current_run_id
            def reclaim():
                with connect() as rival:
                    assert kb.block_task(rival, tid, reason="Reassigned during acceptance")
                    assert kb.unblock_task(rival, tid)
                    github["replacement"] = kb.claim_task(rival, tid).current_run_id
            github.update(conclusion=conclusion, race=reclaim)
            assert not kb.complete_task(conn, tid, expected_run_id=run_id,
                metadata={"published_pr": "https://github.com/acme/repo/pull/7"})
            assert kb.get_task(conn, tid).current_run_id == github["replacement"]
            assert github["replacement"] != run_id
            assert kb.get_task(conn, tid).status != "done"
            assert conn.execute("SELECT count(*) FROM task_events WHERE task_id=? AND kind='pr_acceptance'", (tid,)).fetchone()[0] == 0
            github.pop("race")


def test_plan_gated_rules_require_the_actions_gate_while_other_refusals_stay_infra(github):
    # Private repositories on GitHub Free cannot require checks; for their rules read,
    # gh --paginate --slurp prints exactly this body and exits 1.
    refusal = {"message": "Upgrade to GitHub Pro or make this repository public to enable this feature.",
               "documentation_url": "https://docs.github.com/rest/repos/rules#get-rules-for-a-branch", "status": "403"}
    gated = {"unprotected": True, "rules_error": (403, [refusal]), "name": "All required checks pass", "app": 15368}
    url = "https://github.com/acme/repo/pull/7"
    github.update(gated)
    receipt = collect_acceptance(url, url)
    assert (receipt["ok"], receipt["classification"], receipt["head_sha"]) == (True, "success", "a" * 40)
    assert receipt["required"] == [{"context": "All required checks pass", "app_id": 15368}]
    assert [(c["id"], c["head_sha"], c["classification"]) for c in receipt["checks"]] == [(42, "a" * 40, "success")]
    for fault, expected in (({"missing": True}, "missing"), ({"app": 1}, "missing"),
                            ({"conclusion": "failure"}, "failure"), ({"conclusion": "pending"}, "pending"),
                            ({"stale": True}, "stale"),
                            # Readable classic protection is never replaced by the gate.
                            ({"unprotected": False}, "missing"),
                            # Only the plan refusal is policy; auth, not-found and malformed bodies are not.
                            ({"rules_error": (403, [{**refusal, "message": "Resource not accessible by integration"}])}, "infra"),
                            ({"rules_error": (404, [{**refusal, "message": "Not Found", "status": "404"}])}, "infra"),
                            ({"rules_error": (403, "<html>unavailable</html>")}, "infra")):
        for key in ("missing", "stale"):
            github.pop(key, None)
        github.update({**gated, "conclusion": "success", **fault})
        receipt = collect_acceptance(url, url)
        assert (receipt["ok"], receipt["classification"]) == (False, expected), fault
