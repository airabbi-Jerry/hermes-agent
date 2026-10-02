"""以合成 session 驗證真實 idle 壓縮與持久化，不連接訊息平台。

在 checkout 根目錄執行：.venv/bin/python evals/idle_compaction/live_idle_probe.py
只在本 worktree 的 .hermes/task-artifacts/t_b9d9c6b3 建立隔離 home。
使用執行者 profile 的既有 Codex 登入唯讀解析函式；不儲存或列印憑證、不刷新 token。
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
ARTIFACTS = ROOT / ".hermes/task-artifacts/t_b9d9c6b3"


def write_json(path: Path, value) -> None:
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2, default=str) + "\n", encoding="utf-8")


def facts(messages) -> dict:
    from agent.context_compressor import ContextCompressor
    from agent.model_metadata import estimate_messages_tokens_rough
    return {
        "rows": len(messages),
        "content_chars": sum(len(str(m.get("content") or "")) for m in messages),
        "rough_tokens": estimate_messages_tokens_rough(messages),
        # Conversation 讀回不一定保留記憶體標記；用正式摘要分類器辨識持久化內容。
        "summary_rows": sum(ContextCompressor.classify_summary_content(m.get("content")) is not None for m in messages),
    }


def main() -> int:
    worker_home = os.environ.get("HERMES_HOME")
    if not worker_home:
        raise RuntimeError("缺少執行者 HERMES_HOME；不得借用正式 profile")
    ARTIFACTS.mkdir(parents=True, exist_ok=True)
    home = ARTIFACTS / f"isolated-home-{time.time_ns()}"
    home.mkdir()
    os.environ["HERMES_HOME"] = str(home)
    for key in tuple(os.environ):
        if key.startswith("HERMES_KANBAN_") or key.startswith("HERMES_SESSION_"):
            os.environ.pop(key)
    os.environ.pop("HERMES_PROFILE", None)
    os.environ["TERMINAL_CWD"] = str(ROOT)

    from hermes_cli.config import atomic_config_write
    config = {
        "model": {"provider": "openai-codex", "default": "gpt-5.4-mini", "context_length": 65536},
        "compression": {"enabled": True, "threshold": 0.75, "target_ratio": 0.2,
                        "idle_compact_after_seconds": 7200, "protect_first_n": 0, "protect_last_n": 2,
                        "min_tail_user_messages": 1, "in_place": True, "codex_responses_native": False},
        "auxiliary": {"compression": {"provider": "auto", "timeout": 180}},
        "memory": {"memory_enabled": False}, "curator": {"enabled": False},
        "gateway": {"multiplex_profiles": False},
    }
    atomic_config_write(home / "config.yaml", config)
    from hermes_constants import set_hermes_home_override, reset_hermes_home_override
    from hermes_cli.auth_codex import resolve_codex_runtime_credentials
    from hermes_cli.auth_constants import DEFAULT_CODEX_BASE_URL
    from hermes_cli.codex_models import get_codex_model_ids
    scope = set_hermes_home_override(worker_home)
    try:
        runtime = resolve_codex_runtime_credentials(read_only=True)
        access_token = runtime["api_key"]
        credential_source = runtime["source"]
        del runtime
    finally:
        reset_hermes_home_override(scope)
    from hermes_logging import setup_logging, _stop_queue_listener
    setup_logging(hermes_home=home, mode="gateway")
    models = get_codex_model_ids(access_token=access_token)
    candidates = ("gpt-5.4-mini", "gpt-6.1-sol", "gpt-6-sol")
    model = next((m for m in candidates if m in models), None)
    if not model:
        raise RuntimeError(f"Codex catalog 無適用模型: {models}")
    config["model"]["default"] = model
    atomic_config_write(home / "config.yaml", config)

    from run_agent import AIAgent
    from hermes_state import SessionDB
    from gateway.run import GatewayRunner

    from agent.context_compressor import ContextCompressor

    sid = "idle-closeout-synthetic"
    db = SessionDB(db_path=home / "state.db")
    db.create_session(sid, source="cli", model=model)
    observation = ("Synthetic rehearsal only: item checked, isolation boundary intact, no production action. "
                   "The test parcel stays inside this checkout. ")
    for i in range(32):
        db.append_message(sid, "user", f"Rehearsal record {i}:\n" + observation * 25)
        db.append_message(sid, "assistant", f"Record {i} acknowledged. No external delivery or production change.")
    old_stamp = time.time() - 10800
    db.touch_session_activity(sid, old_stamp)
    history = db.get_messages_as_conversation(sid)
    write_json(home / "context-before.json", history)
    before_facts = facts(history)
    statuses = []
    agent: Any = AIAgent(
        model=model, provider="openai-codex", api_mode="codex_responses",
        base_url=DEFAULT_CODEX_BASE_URL, api_key=access_token, session_id=sid, session_db=db,
        enabled_toolsets=[], quiet_mode=True, skip_context_files=True, skip_memory=True,
        skip_background_review=True, max_iterations=3, run_budget_seconds=300,
        reasoning_config={"effort": "low"}, cwd=str(ROOT),
        status_callback=lambda event, message: statuses.append({"event": event, "message": message}),
    )
    del access_token
    assert isinstance(agent.context_compressor, ContextCompressor)
    # 只移動測試 session 的閒置時鐘；不改判斷、估算、compress 或 API 呼叫。
    agent._last_activity_ts = old_stamp
    GatewayRunner._init_cached_agent_for_turn(agent, interrupt_depth=0)
    clock = {"reference_ts": agent._idle_compact_reference_ts, "reset_activity_ts": agent._last_activity_ts,
             "gap_seconds": agent._last_activity_ts - agent._idle_compact_reference_ts}
    cc = agent.context_compressor
    before_count = cc.compression_count
    result = agent.run_conversation(
        "This is an isolated synthetic rehearsal. Reply with IDLE_PROBE_OK only. Do not use tools.",
        system_message="You are an isolated verification assistant. No tools or external deliveries.",
        conversation_history=history,
    )
    write_json(home / "context-after-live.json", result["messages"])
    after_count = cc.compression_count
    fallback_used = cc._last_summary_fallback_used
    telemetry = cc._last_compression_telemetry
    agent.close()
    db.close()
    reopened = SessionDB(db_path=home / "state.db")
    durable = reopened.get_messages_as_conversation(sid)
    row = reopened.get_session(sid)
    stored_messages = reopened.get_messages(sid)
    write_json(home / "context-after-readback.json", durable)
    write_json(home / "messages-after-readback.json", stored_messages)
    write_json(home / "session-readback.json", row)
    reopened.close()
    _stop_queue_listener()
    log_path = home / "logs/agent.log"
    log = log_path.read_text(encoding="utf-8")
    idle_lines = [line for line in log.splitlines() if "Idle compaction:" in line]
    preflight_lines = [line for line in log.splitlines() if "Preflight compression:" in line]
    attempt_lines = [line for line in log.splitlines() if "context compression attempt telemetry:" in line]
    attempts = [json.loads(line.split("context compression attempt telemetry: ", 1)[1]) for line in attempt_lines]
    receipt = {
        "checkout": str(ROOT), "head": subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip(),
        "home": str(home), "session_id": sid, "model": model, "clock": clock,
        "credential_source": credential_source, "credential_home": worker_home,
        "idle_after_seconds": agent.compression_idle_compact_after_seconds,
        "threshold_tokens": cc.threshold_tokens, "floor_tokens": int(cc.threshold_tokens * cc.summary_target_ratio),
        "before": before_facts, "after_live": facts(result["messages"]), "after_readback": facts(durable),
        "durable_summary_flags": sum(bool(m.get("_compressed_summary")) for m in stored_messages),
        "compression_count_before": before_count, "compression_count_after": after_count,
        "fallback_used": fallback_used, "telemetry": telemetry, "attempts_readback": attempts,
        "completed": result.get("completed"), "final_response": result.get("final_response"),
        "idle_log_lines": idle_lines, "preflight_log_lines": preflight_lines, "statuses": statuses,
        "same_session": agent.session_id == sid,
    }
    checks = {
        "idle_log_exists": len(idle_lines) == 1,
        "clock_survived_gateway_reset": clock["gap_seconds"] >= 10800,
        "real_compaction_completed": after_count == before_count + 1 and not fallback_used and any(a.get("commit_status") == "committed" for a in attempts),
        "no_threshold_preflight": not preflight_lines,
        "turn_completed": bool(result.get("completed")) and "IDLE_PROBE_OK" in str(result.get("final_response")),
        "same_session": receipt["same_session"],
        "context_shrank": facts(durable)["rough_tokens"] < before_facts["rough_tokens"],
        "durable_summary_exists": facts(durable)["summary_rows"] > 0,
        "summary_content_matches": [m["content"] for m in durable if ContextCompressor.classify_summary_content(m.get("content"))]
        == [m["content"] for m in result["messages"] if ContextCompressor.classify_summary_content(m.get("content"))],
        "durable_reply_exists": any(m.get("role") == "assistant" and "IDLE_PROBE_OK" in str(m.get("content")) for m in durable),
    }
    receipt["checks"] = checks
    write_json(home / "receipt.json", receipt)
    write_json(ARTIFACTS / "latest-probe.json", receipt)
    print(json.dumps(receipt, ensure_ascii=False, indent=2, default=str))
    return 0 if all(checks.values()) else 1


if __name__ == "__main__":
    raise SystemExit(main())
