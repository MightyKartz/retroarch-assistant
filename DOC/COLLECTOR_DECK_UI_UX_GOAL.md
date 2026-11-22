# GOAL：RetroArch 助手 Precision Collector Deck UI/UX 改造落地

你是资深前端工程师，负责将本仓库的 RetroArch 助手桌面应用按照《Precision Collector Deck UI/UX 改造实施工单》（`DOC/COLLECTOR_DECK_UI_UX_TICKETS.md`）完成全部六个批次（CD-01 → CD-06）的 UI/UX 改造，并以 `output/ui-preview/retroarch-assistant-next.html` 作为视觉与交互基准（浏览器打开体验，非逐像素照抄）。最终交付：改造落地 + 全量回归通过 + 桌面应用打包重装 + 截图存档 + 最终自审报告。

## 一、执行环境

- 仓库：`/Volumes/Untitled/Dev/Playlistcn`，位于 ExFAT 卷。**每次运行测试、打包、git 操作之前必须先执行 `find . -name '._*' -delete`**（含 `.git` 与 `dist`），否则 `check_ui_js.py`、codesign、git index 会失败。
- 工作区存在**未提交的前序改动**（替换语义简化 + 单行状态栏反馈 + `showModal` 无默认焦点 + AGENTS.md 等），属于本次改造的既有基础：先 `git status` / `git diff` 核对并保留，不得回退，也不得把无关变更混入提交。
- `output/`、`dist/`、`build/`、`release-video/` 为未跟踪/已忽略目录，勿 `git add`。
- Node 单测中 `browser_smoke.cjs` 需要 WebSocket 参数，不可独立运行，由 `python3 -m pytest`（真 Chrome）驱动。
- 打包 venv 固定为 APFS 上的 `~/.venvs/plcn-build`。

## 二、动手前必读（按顺序通读，缺一不可）

1. `DOC/COLLECTOR_DECK_UI_UX_TICKETS.md` —— 唯一权威工单：CD-01…CD-06 范围、实施内容、验收标准。
2. `output/ui-preview/retroarch-assistant-next.html` —— 视觉基准原型。
3. `AGENTS.md` —— AppleDouble、测试、打包、签名、`ditto` 拷贝约定。
4. `src/templates/plcn.html`、`plcn.css`、`workbench.css` —— 现有 DOM 与样式约定。
5. `src/templates/workbench.js`（`showModal`/`wbElement`/`wbIcon`/`wbCoverFrameRatio`/`renderPlatformFan`/`renderWorkbenchOverview`）、`game-files.js`（C-03n 并发保护）、`game-browser.js`（`renderGameInspector`/`gameLibrary.selection`）、`preview.js`（`currentChanges`/`buildPreviewEditor`）、`library.js`、`library-navigation.js`、`game-management.js`、`desktop.js`、`ui.js`。
6. `tests/python/test_ui_information_architecture.py`、`test_ui_localization.py`、`tests/browser_smoke.cjs` —— 结构/i18n/冒烟断言的现状与更新点。
7. `DOC/BETA2_UI_UX_PLAN.md`、`DOC/GAME_DETAILS_UX_REFINEMENT.md`、`DOC/PREVIEW_STATE_CONTRACT.md` —— 按钮约定、详情页细化、预览状态契约。

## 三、不可突破的红线（违反即返工）

1. **零构建、完全离线**：只改 `src/templates/` 下原生 HTML/CSS/JS；禁止外部 CDN、网络字体、图标库、打包器。
2. **请求与图标**：一律 `desktopRequest`（desktop.js），禁裸 `fetch` 与原生 `alert`；图标一律 `wbIcon`/`icon` + `workbench-icons.svg`（24 网格、1.7 线宽），新图形先入 sprite，禁散装内联 SVG。
3. **模态与焦点**：所有 `<dialog>` 经全局 `showModal(dialog)` 打开——不默认选中任何按钮、焦点落标题；主入口为输入框的弹层（如命令面板）在 `showModal` 后显式聚焦。
4. **双语门禁**：`plcn.html` 新增中文可见文本及含中文的 `placeholder`/`title`/`aria-label` 必须同步 `data-i18n*` 标记并在 `ui.js` 登记英文，`test_ui_localization.py` 必须全绿。
5. **保护已收敛状态机**：
   - `game-files.js`：C-03n 视图/导航/批次三层归属校验、`game-file-overwrite-dialog`、无驻留排他替换、`runOpsJob` 进度、`.game-files-status` 单行状态栏（读错保留+摘要追加）；不恢复详情块、不恢复「重新检测存储」。
   - `library.js`/`game-browser.js`：封面与列表视图共用同一个 `gameLibrary.selection`。
   - `desktop.js`/`tasks.js`：`workbench.operation` 互斥锁与 `syncIdleButtons()` 空闲门控。
6. **视觉禁令（防流水线感）**：禁渐变文字、禁 >1px 侧色条、禁常规卡片 1px 边框+大阴影叠加（≥16px 阴影仅限 dialog/弹出菜单）、禁 ≥20px 卡片圆角、禁条纹/噪点装饰背景、禁全大写正文。

## 四、执行顺序与每批工作流

顺序：`CD-01 → CD-02 → CD-03 → CD-04 → CD-05 → CD-06`，每批独立可提交，不得跳批或一次跨多批改动。

**每完成一个工单，严格走完闭环：**

1. **修改前思考一轮**：列出本工单涉及的现状代码段、受影响断言、预期 diff 范围与风险点；逐条对照工单验收标准。先想清楚再动手。
2. **实现**：最小化 diff，沿用现有代码风格与命名；同步更新 i18n 标记、测试断言与 Node harness stub——**断言按新语义更新，禁止为通过而放宽**。
3. **验证（失败即修复重来）**：
   ```sh
   find . -name '._*' -delete
   python3 scripts/check_ui_js.py
   for f in tests/*.test.cjs; do node "$f" || exit 1; done
   python3 -m pytest
   git diff --check
   ```
4. **自审并修改（必须显式输出审查清单与结论）**：通读本工单全部 `git diff`，逐项核对：
   - 红线：裸 `fetch`/`alert`/散装 SVG/裸 `showModal`/缺 `data-i18n`；
   - 语义回归：C-03n、selection 共享、互斥锁、详情页返回焦点、`gameDetailsReturnTop`、深浅主题变量、断点切换；
   - 视觉与可访问性：对比度 ≥4.5:1（含 placeholder）、长中英文与长路径截断/换行、`focus-visible`、`prefers-reduced-motion`、`prefers-reduced-transparency`；
   - 测试漂移：是否有断言被放宽而非按新语义更新；
   - 死代码与命名残留。
   发现问题当场修复并回到第 3 步重新验证；确认无问题后，在工单文档中勾掉对应 `[x]` 并记录实际结果，再进入下一工单。

## 五、全部工单完成后的总验收

1. **回归**：全部 pytest（含真 Chrome 冒烟）、全部 Node 单测、`check_ui_js.py`、`git diff --check` 全绿。
2. **打包重装**：`~/.venvs/plcn-build/bin/python scripts/package_desktop.py --arch arm64`；codesign 因 `._*` 失败时先 `find dist -name '._*' -delete` 再手动 ad-hoc 重签并 `--verify --deep --strict`；**一律用 `ditto`（禁止 `cp -R`）** 同步到 `/Applications/RetroArch Assistant.app` 与 DMG staging；`hdiutil create` 失败时加 `-fs HFS+`；self-check 输出 `"ok": true`。
3. **视觉复核**：1440×900 与 780×900 × 中文/English × 深色/浅色共 8 组截图归档 `output/ui-preview/`，逐一核对对比度、焦点环、溢出截断、无默认按钮焦点。
4. **最终自审报告**：逐项列改动文件与目的、审查中发现并已修复的问题、遗留风险与建议；`git status` 保持在可提交状态，是否 commit/开 PR 由用户决定。

## 六、完成定义（Done）

六个工单全部实现且验收项勾选完成 + 全量回归通过 + 应用打包重装 + 8 组截图归档 + 最终自审报告输出。设计取舍与原型冲突时，以工单红线和工程稳定性优先。
