# 本地升級保留清單：idle compaction

對象：upgrade/v0.21.5-local；任務 t_b9d9c6b3，承接 t_d63974bf。

必須保留的原始 commit：6bedd3e207282caf0de05d3f04c854dc6c050149。
來源：jerry-fork 的 fix/idle-compaction-closeout-t_b9d9c6b3。
PR：https://github.com/airabbi-Jerry/hermes-agent/pull/2
PR 的 base：upgrade/v0.21.5-local。

此卡採「列入升級清單」，不移動其他 worktree 正在使用的本地 upgrade 分支。
upgrade 基準 58bc44fa8d18b2c5d6b59862aa36b2d336df190a 已推送到 fork，
提供此 PR 明確的 review／合併目標；此卡不自行合併 PR。

下次升級由 HERMES-OPS 在升級 worktree 執行，正式 checkout 不在本卡操作範圍：

    git fetch jerry-fork
    git cherry-pick 6bedd3e207282caf0de05d3f04c854dc6c050149
    scripts/run_tests.sh tests/gateway/test_idle_compaction_gateway_clock.py tests/agent/test_idle_compaction.py tests/agent/test_idle_compaction_lock_and_guards.py tests/hermes_cli/test_send_cmd.py

若已合併此 PR，使用 git merge-base --is-ancestor 確認原始 commit，避免重複套用；
若採 cherry-pick，SHA 會不同，需確認 patch-id 及上述行為測試，不能只比 SHA。
升級完成前必須確認 gateway 交出前一輪活動時鐘、fresh session 讀 durable last_activity_at、
idle 設定使快取失效，以及 hermes send --no-mirror 仍在。

本卡不新增 schema migration，不修改正式 profile 設定，不重啟正式 gateway。
