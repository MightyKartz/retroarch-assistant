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

## 后续更新：访达式同名提示、隐藏过滤与替换语义（main 未提交改动）

在 C-03n 关闭之后，文件页 UX 与替换实现按访达语义进一步简化：

- **同名处理**：逐项勾选 + stamp 指纹回传的导入对话框替换为单一提示「取消 / 跳过同名项 / 替换」。替换发送 `overwrite: true`；跳过与取消不提交任何覆盖意图。预览请求带 `light: true`，不再计算目标指纹（`preview_import(..., light)`），MTP 目录树上更快。`decisions`/stamp 机制服务端保留（含测试），当前客户端不发送。
- **替换不保留原件**：`_publish_replace` 改为先删原目标、再以 `_rename_exclusive` 排他发布暂存件——不再把原件驻留 `.plcn-old-*`，用户无需事后手动清理。删除后若目标名被外来者抢占（或 MTP 上原件删除失败仍占位），暂存件不会被改名覆盖他人内容，而是留在 `*.plcn-*` 暂存路径并经 `result.leftovers` 报告；本地 claim-write 失败时目标占位与暂存件两条路径一并报告。`PublishFailed` 与回滚/驻留分支随之移除，`test_game_file_ops.py` 中全部驻留语义用例改写为「原件已删 + 暂存件存活报告」。
- **同批同名来源**：预览按目标名大小写归组的 `duplicates` 不再弹窗选择；取列表中首个来源为其名提交，其余来源不发送（按项索引剔除，`conflicts` 同样只保留将提交的项，名单不重名）；对话框在有剔除项时补注被略过的来源数量。
- **隐藏文件**：`/api/game-files/directory` 过滤 `.` 开头项（`.DS_Store`、`._*`、隐藏目录）。选择器经原生对话框进入隐藏目录不受影响。
- **反馈收敛为单行状态栏**：结果详情块（`game-files-detail`）及其 DOM/CSS 全部移除；写入摘要、残留路径、失效通知都落在 `.game-files-status` 一行。读取错误保留该行时，后续摘要与失效提示**追加**在错误之后（`presentResult` 的 `lastReadError`/`append` 分支），不静默丢弃也不覆盖错误；四格矩阵与 C-03j/k/l/m 冒烟断言同步改为状态栏断言。
- **对话框无默认选中**：`showModal()` 后 WebKit 会自动聚焦首个按钮，现统一在 `showModal` 后把焦点从对话框控件上移走（`game-files.js` 的模块级 `showModal` 助手与 `desktop.js` 共享确认框同步处理）；重命名对话框仍显式 `input.focus()`。
- **侧栏**：「重新检测存储」按钮移除；打开视图时仍自动检测，`storageStatus` 的检测中/错误提示保留。
- **对话框标识**：类名/标题 id 更名 `game-file-overwrite-dialog` / `game-file-overwrite-title`，smoke 选择器同步；「跳过同名项」「替换」挂 `game-file-skip` / `game-file-replace` 便于测试定位。

回归：`pytest` 全量（含替换断言 `overwrite:true`、无驻留件、暂存存活报告、light 预览、隐藏过滤用例）；`check_ui_js` 与全部 node 单测通过。
