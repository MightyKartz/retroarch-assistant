# C-03n 反馈呈现与四格回归 · 实施与自审结论

**实施与自审负责人 Devin**　对应工单：[C-03n 原工单](GAME_FILES_ENHANCEMENT_FINDER_QUEUE_FEEDBACK_MATRIX.md)、[续单](GAME_FILES_ENHANCEMENT_FINDER_QUEUE_FEEDBACK_MATRIX_FOLLOWUP.md)。审查基线为 `b3b00c2`，实施随本提交一并进入仓库。

## 实施项

1. **统一反馈呈现**　`presentResult`（`src/templates/game-files.js`）是状态栏与操作详情的唯一呈现入口。三条调用路径均经由此入口：
   - 导航结算：`loadDirectory` 成功分支恢复暂存的写入结果与失效提示，失败分支在保留读取错误的同时把暂存结果并入详情块；
   - 写入完成：`presentWriteResult` 按刷新结果选择「摘要上状态栏」或「结果进详情块、错误留状态栏」；
   - 批次失效：`showDropExpired` 在无旧结果无错误时走纯状态消息，否则经 `presentResult` 并入详情 lead，不掩埋读取错误或已结算写入结果。
   归属校验保留：视图（`gameFilesView !== view`）、目录请求（`request !== latestNav` / `navApplied`）与批次（`writeVersion` / `navVersion` / `batch.context`）三层，迟到响应只更新仍有效的状态。
2. **四格共用验收断言**　`tests/browser_smoke.cjs` 中 `assertBatchOnce`（恰一次提交、来源与落点归属第一批、目标字节一致、第二批目标不存在）与 `recoverCheck`（恢复导航后列出第一批文件、无提示重播、无额外提交）由 NavM/NavN/NavP/NavQ 四格共用；每格独立临时目录与文件，`__refReady`/`__navReady`/`__navFail` 显式等待点控制结算顺序。

## 四格验收结果

| 新导航结果 | 先结算的请求 | 用例 | 结果 |
| --- | --- | --- | --- |
| 失败 | 新导航 | NavM（C-03l）| 读取错误留状态栏，第一批结果与失效提示同在详情块；`assertBatchOnce`、`recoverCheck` 通过 |
| 失败 | 旧刷新 | NavN（C-03m）| 等待期失效提示与读取错误均保留；同上断言通过 |
| 成功 | 旧刷新 | NavP（C-03n cell 1）| 落在新目录，写入摘要恢复，失效提示在详情块；同上断言通过 |
| 成功 | 新导航 | NavQ（C-03n cell 2）| 落在新目录，两类反馈均可查；同上断言通过 |

同目录（C-03a）与不同目录（C-03b）有效批次串行写入复验通过：各自恰一次提交、落点正确、第二批在第一批完成后才开始。以上用例由 `test_browser_smoke.py` 在真实 Chromium 中执行，随本次 pytest 全量通过。

## 自审结论与发现

自审覆盖 `game_file_actions.py` 全量重写、`api_library.py` 路由分流、`desktop_window.py` 拖放捕获、`content_scan.py` 保留名过滤、`mtp_runtime.py` modified 字段、四格等待点与断言。范围内无未解决发现；一条**既有问题**（非本次引入）记录如下，建议另立工单：

- **旧有：`apply_copy` 本地目标非原子认领。** `target.mkdir()` / `shutil.copy2` 之间若被外来同名抢占，异常分支的 `_remove_destination` 会误删他人目录内容，文件路径则会静默覆盖。`apply_add` 已用暂存加 `_rename_exclusive` 杜绝此类问题，`apply_copy`（含 `apply_copy_batch`）仍沿用旧写法。窗口窄、触发需外部并发写，建议将本地复制同样改为「暂存 + 排他发布」。

## 最终测试摘要

- `python3 -m pytest`：726 项通过（含四格浏览器回归与 `test_game_file_ops.py` 新增 50 项发布/指纹/保留语义词例）。
- `python3 scripts/check_ui_js.py`：22 个脚本块、132 个事件处理器语法通过。
- `node tests/*.test.cjs`：14 个文件、170 项断言全部通过。
- `git diff --check`：无空白问题。提交前已清理 `._*` AppleDouble 与仓库根 `tmp*` 残留目录。

平台验收项（Windows 跨目录粘贴、MTP 真机特殊文件名）未在本环境覆盖，仍待真机核对。
