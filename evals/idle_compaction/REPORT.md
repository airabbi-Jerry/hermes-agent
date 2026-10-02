# t_b9d9c6b3 交件報告

Checkpoint：交付 HERMES-OPS review；不合併 PR、不更新正式 gateway。

## 交付

- 原始修正 `6bedd3e207282caf0de05d3f04c854dc6c050149` 保留在
  `jerry-fork/fix/idle-compaction-closeout-t_b9d9c6b3` 的歷史中。
- PR：https://github.com/airabbi-Jerry/hermes-agent/pull/2
  head：`fix/idle-compaction-closeout-t_b9d9c6b3`；base：`upgrade/v0.21.5-local`。
- 本卡選擇「列入升級保留清單」，詳見同目錄 `UPGRADE.md`。
  upgrade 分支仍在 `58bc44fa8d18b2c5d6b59862aa36b2d336df190a`；尚未合併此修正，
  不把「已列清單」說成「已部署升級分支」。
- 新增 `live_idle_probe.py`，以真實 `AIAgent`、`ContextCompressor`、Codex API、
  `SessionDB` 執行隔離合成 session。只人為調整測試 session 的活動時間；
  gateway 使用真實 `_init_cached_agent_for_turn`，沒有 mock 壓縮、API、context 估算或 DB。

## 逐條自評

| 條件 | 結果 | 證據 |
| --- | --- | --- |
| A1：原始 commit 推 fork、新 PR、URL/head/base 與包含關係讀回 | PASS | PR #2；`final-pr-readback.json`、`final-fork-refs.txt`、`final-original-ancestry.txt`（exit 0） |
| A2：upgrade 升級保留方式可讀回 | PASS | `UPGRADE.md` 已列完整原始 SHA、來源、cherry-pick 指令、測試及重複套用檢查；PR base 為 upgrade 分支 |
| A3：隔離 session 拉長閒置、真實回合觸發 idle、日誌及 context/session 讀回 | PASS | 下列真實日誌；`latest-probe.json` 全部檢查為 true；隔離 home 的 context-before/after-live/after-readback、messages-after-readback、session-readback、state.db |
| A4：相關既有測試通過並保存輸出 | PASS | `tests.txt`：4 檔、38 tests passed、0 failed，file-retries=0；`ruff.txt`：All checks passed |
| A5：fetch 最新 origin、main 與同 PR repository 全部 open PR 的合併模擬、migration 自評 | PASS（模擬及揭露；main 有衝突） | 逐組結果見下節及 `final-git-receipt.json`；fork API 分頁與 CLI 清單均為 2 張 PR；本卡未新增 migration |
| A6：不 commit 正式 checkout、不重啟正式 gateway、不碰 Saeko 正式 profile/session、交件 worktree 乾淨 | PASS | probe 所有輸出在此 worktree 的隔離 home，僅唯讀解析 codex-worker 既有登入、不寫出憑證；`final-git-status.txt` 空白、`final-git-receipt.json` 記錄工作目錄與最終 head |

本卡沒有 FAIL／未做的驗收項目。review、PR 合併及日後升級部署交由 HERMES-OPS；
這些是刻意保留的後續操作，不宣稱已完成。

## 真實觸發及持久化

成功的隔離 home：

    /Users/jerrylin/.hermes/hermes-agent-wt/idle-compact-fork/.hermes/task-artifacts/t_b9d9c6b3/isolated-home-1790979887817072000

`logs/agent.log:34`：

    2026-10-03 06:24:50,094 INFO [idle-closeout-synthetic] agent.turn_context: Idle compaction: 10800s idle >= 7200s, ~28,783 tokens > 11,141 floor (last compaction produced ~n/a) (session idle-closeout-synthetic)

`logs/agent.log:40–41`：壓縮 `messages=65->31`（含當輪 user），
`commit_status=committed`、`split_status=in_place_committed`、`fallback_used=false`。
壓縮後仍接續實際模型回合，回覆 `IDLE_PROBE_OK`，`tool_turns=0`。

gateway reset 前後保留的閒置差為 10800.255459 秒；設定觸發值為 7200 秒。
context 觸發估算 28,783 tokens，低於 token preflight 門檻 55,705；日誌沒有
`Preflight compression:`，排除把 token-threshold 觸發誤認為 idle。

| 讀回 | 訊息筆數 | 內容字元 | 粗估 tokens | 摘要筆數 |
| --- | ---: | ---: | ---: | ---: |
| DB 載入的回合前 context | 64 | 109132 | 28214 | 0 |
| 回合後 live context（含當輪回覆） | 32 | 82714 | 21300 | 1 |
| 關閉並重開 SessionDB 後 context | 32 | 82714 | 21277 | 1 |

live／durable 摘要內容相同，durable `_compressed_summary` 為 1 筆，session id 不變。
粗估 token 差異來自讀回時訊息 metadata 不同；前後均使用同一估算函式，沒有宣稱這些是 API 精確用量。

先前試跑也保留在證據目錄：較短 context 曾遭既有 would-grow guard 拒絕，不能當作成功壓縮；
後續一次已成功提交但探針誤以 conversation API 不保留的記憶體標記判斷摘要。
最後版本改用正式摘要分類器及 DB 訊息旗標讀回，成功執行的 exit code 為 0。
未修改正式壓縮實作以讓驗證過關。

## 合併模擬

`origin` 是 `NousResearch/hermes-agent`；PR repository 是 `airabbi-Jerry/hermes-agent`。
因此「同 repo 的 open PR」以 PR repository 的 `gh pr list --repo ...` 及完整分頁 API
讀回為準：#1、#2，共 2 張，不把上游其他貢獻者的 PR 納入本 fork 卡片範圍。

先執行 `git fetch origin`，再 `git merge-tree --write-tree origin/main HEAD`。
為原樣執行卡片的 `origin/<PR 分支>` 指令，將這兩個 fork head fetch 到同名 remote-tracking
別名；驗證別名 SHA 與 GitHub PR head SHA 一致。上游沒有這兩個同名分支，未變更 origin URL，
也未移動任何本地 `refs/heads`。每組原始指令與輸出均保存在 `final-git-receipt.json`。

| 組別 | 結果 |
| --- | --- |
| 最新 `origin/main` + HEAD | 衝突：`hermes_cli/kanban_pr_acceptance.py`、`hermes_cli/update_cmd_fleet.py`、`tests/gateway/test_feishu.py`、`tests/hermes_cli/test_kanban_pr_acceptance.py`、`tests/hermes_cli/test_send_cmd.py`、`website/docs/user-guide/features/kanban.md` |
| fork PR #1：`origin/fix/kanban-pr-acceptance` + HEAD | 乾淨 |
| fork PR #2：`origin/fix/idle-compaction-closeout-t_b9d9c6b3` + HEAD | 乾淨 |
| PR 目標 `jerry-fork/upgrade/v0.21.5-local` + HEAD | 乾淨 |

main 衝突原因：本 worktree 基於本地 v0.21.5 系列，帶有既有 Kanban、fleet、Feishu 修正，
與持續前進的上游 main 重疊；send_cmd 測試也與上游 fixture／測試整理重疊。
本卡的 PR 目標是本地 upgrade 分支，該組及 fork 其他 open PR 均乾淨。
為保留原始 commit 並避免越界處理全量上游升級，本卡不把 main 合併進來、不修改上述衝突檔案。
HERMES-OPS 日後整合上游 main 時需處理這六檔，不能宣稱與 main 無衝突。

未新增 schema migration；沒有新 migration 編號需要分配或撞號比對。

## 證據目錄及重跑

全部本機證據根目錄：

    /Users/jerrylin/.hermes/hermes-agent-wt/idle-compact-fork/.hermes/task-artifacts/t_b9d9c6b3

重跑既有測試：

    scripts/run_tests.sh tests/gateway/test_idle_compaction_gateway_clock.py tests/agent/test_idle_compaction.py tests/agent/test_idle_compaction_lock_and_guards.py tests/hermes_cli/test_send_cmd.py --file-retries 0

重跑隔離真實驗證（需要執行者既有 Codex 登入，會發出真實模型請求）：

    .venv/bin/python evals/idle_compaction/live_idle_probe.py

每次重跑建立新的隔離 home，不修改正式 session。原始 context／DB 不推到 GitHub；
遠端僅提交探針、升級清單及這份去憑證的驗收報告。
