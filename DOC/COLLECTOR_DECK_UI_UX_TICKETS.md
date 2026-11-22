# RetroArch 助手 · Precision Collector Deck UI/UX 改造实施工单

**状态**：待实施  
**交互参考原型**：`output/ui-preview/retroarch-assistant-next.html`（含库总览、画廊+滑出检视台、补全校对、游戏文件、`⌘K` 命令面板及深浅双主题）  
**关联既有计划**：[beta2 桌面界面计划](BETA2_UI_UX_PLAN.md) · [游戏详情页 UX 实施单](GAME_DETAILS_UX_REFINEMENT.md) · [补全预览状态契约](PREVIEW_STATE_CONTRACT.md) · [游戏文件四格反馈闭环](GAME_FILES_ENHANCEMENT_FINDER_QUEUE_FEEDBACK_MATRIX_CLOSURE.md)

---

## 一、改造目标与全局架构红线

将现有偏通用后台表格感的界面，渐进升级为具备阳极氧化铝硬件层次、掌机遥测感知与空间连续性的 **Precision Collector Deck（精密掌机收藏舱）**，同时确保现有 **727 项 Python 回归、169 项 Node 单测与 22 块前端脚本检查**平滑演进。

### 不可突破的工程与交互红线

1. **零构建与完全离线可用**：保持 `src/templates/*.js` + `plcn.css` + `workbench.css` 原生加载架构，严禁引入外部字体 CDN、远程图标库或打包工具。
2. **既有基础设施复用**：
   - 所有后端请求一律经由 `desktopRequest`（`src/templates/desktop.js`），严禁裸 `fetch` 或原生 `alert`。
   - 所有图标一律通过 `wbIcon(name)` / `icon(name)` / `data-ui-icon` 引用 `src/templates/workbench-icons.svg`（Tabler 24 网格、`stroke-width="1.7"`），新增图形先补入该 sprite，禁止内联散装 SVG。
   - 所有模态 `<dialog>` 打开一律调用全局 `showModal(dialog)`（`src/templates/workbench.js`），保持**不默认选中任何按钮、焦点落于标题**（命令面板等明确以搜索框为主入口的弹层除外，须在 `showModal` 后显式聚焦输入框）。
3. **双语静态标记门禁**：`src/templates/plcn.html` 中新增的任何中文可见文本及含中文的 `placeholder`、`title`、`aria-label`，必须同步补齐 `data-i18n*` 属性并在 `src/templates/ui.js` 登记英文词条，确保 `tests/python/test_ui_localization.py` 全绿。
4. **保护已收敛的高风险状态机**：
   - `game-files.js`：保留 C-03n 视图/导航/批次三层归属校验、`game-file-overwrite-dialog` 访达式同名提示、无驻留排他替换、`runOpsJob` 进度弹窗及单行 `.game-files-status` 错误保留与摘要追加契约；不恢复详情块与「重新检测存储」按钮。
   - `library.js` / `game-browser.js`：封面与列表视图继续共用同一个 `gameLibrary.selection`（`Set`）。
   - `desktop.js` / `tasks.js`：保留 `workbench.operation` 互斥锁与 `syncIdleButtons()` 空闲门控。

### 推荐实施批次与依赖顺序

`CD-01（色彩材质与排版地基）` → `CD-02（侧栏掌机遥测坞与导航统一）` → `CD-03（库总览收藏舱与工具坞）` + `CD-04（盒封画廊与右侧滑出检视抽屉）` → `CD-05（一键补全可视化对照台）` → `CD-06（⌘K 命令面板与文件台融合）`。每个工单均可独立提交、独立回归验收。

---

## 二、分项实施工单

### CD-01 · OKLCH 四阶材质色彩体系与精密排版地基

**现状问题**
- `src/templates/plcn.css` 深色模式下 `--bg: #0F1115` 与 `--surface: #1B1D21` 几乎同阶，且 `--primary` 被弱化为灰白色 `#D4D7DD`，丢失 `brand.svg` 的电光钴蓝与荧光薄荷绿品牌识别度。
- `CRC32`、路径、文件大小、游戏计数未统一等宽对齐，列表刷新或数值变化时产生横向抖动；按钮点击缺乏即时物理按压反馈。

**实施内容**
1. **双主题四阶表面 Token**：在 `plcn.css` 重构 `:root`（浅色「精密陶瓷银」）与 `body[data-theme="dark"]`（深色「阳极氧化铝」），按 `rail（侧栏底座） → canvas（主画布） → surface（承载面） → elevated（悬停/浮层）` 建立四阶明度差（提供 Hex 回退 + `@supports (color: oklch(0 0 0))` 渐进增强）。
2. **微高光边缘与克制阴影**：卡片与面板统一采用 `1px solid var(--border)` 配合顶部内高光 `--bevel-top: inset 0 1px 0 oklch(1 0 0 / 0.065)` 表达厚度；禁止在同一常规卡片上叠加 `1px` 边框与大范围模糊阴影（`≥16px` 浮层阴影仅限 `<dialog>` 与弹出菜单）。圆角收敛为控件 `6px`–`8px`、卡片/面板 `12px`–`14px`。
3. **双信号色分工**：深色模式恢复电光钴蓝为主行动与焦点色（`--primary` / `--primary-fill`），新增 `--phosphor` / `--phosphor-soft`（品牌荧光薄荷绿）专用于「设备已连接 / 100% 完整 / 已省空间」正向状态；保留 `--dark-active-bg`、`--dark-selected-rail` 等既有变量映射。
4. **排版与即时按压反馈**：新增 `--font-mono` 等宽栈，对 `.system-game-count`、路径、字节大小、CRC32 全面开启 `font-variant-numeric: tabular-nums`；为 `.btn:active:not(:disabled)` 添加 `transform: scale(0.975)`（`90ms` 指数缓出），并在 `@media (prefers-reduced-motion: reduce)` 下关闭位移缩放。

**涉及文件**
- `src/templates/plcn.css`、`src/templates/workbench.css`
- `tests/python/test_ui_information_architecture.py`（核对 `test_dark_theme_active_states_are_restrained`）

**验收标准**
- [ ] 深色与浅色主题下正文字体对比度 ≥ 4.5:1，次要说明与输入框占位符清晰易读，无发灰洗白感。
- [ ] 不改动任何 HTML 结构的前提下，现有 727 项 pytest 与 169 项 Node 测试直接通过。

---

### CD-02 · 侧栏「实体掌机遥测坞舱」与四象限一级导航统一

**现状问题**
- 顶栏 `#workbench-device` 与 `#workbench-connection` 仅占一行小字，`#storage-scope` 与设备信息分离，缺乏掌机管理工具的硬件临场感。
- 「游戏文件」与「扫描掌机游戏」缩在面板右上角 `36×36` 纯图标按钮中；进入「游戏文件」时 `.showing-game-files` 直接隐藏整个左侧主机分类树，造成「两个独立软件来回跳」的割裂感。

**实施内容**
1. **侧栏顶部硬件遥测坞（`.hardware-dock`）**：
   - 在 `.sidebar` 品牌区下方新增硬件状态卡片，将 `#workbench-device`、`#workbench-connection`（升级为荧光绿在线呼吸点 / 离线灰点）及 `#storage-scope`（`全部 / 内部 / 存储卡` 分段开关）内聚到同一卡片内，保留原元素 ID 与 `setStorageScope()` 绑定。
   - 当当前库存在可读取容量信息（本地卷 / SD 卡 / MTP 存储）时，展示内部存储与存储卡双色分段容量条及已用/总计等宽读数；无法获取总容量时平滑折叠容量条，不留空白占位。
2. **统一一级工作流导航**：
   - 在硬件坞下方提供清晰带文字的一级模式入口：`库总览`、`主机列表`、`补全校对`（显示待处理计数徽章）、`游戏文件`，支持 `⌘1`–`⌘4`（Windows/Linux 为 `Ctrl+1`–`4`）快捷切换（输入框聚焦时不拦截）。
   - 打开「游戏文件」时保留左侧品牌区、硬件遥测坞与一级模式导航；原 `#game-files-sidebar` 内的存储卷与根目录按钮内迁至 `#game-files-workspace` 左列（窄屏自动折叠为横向芯片栏）。保留 `#game-files-button` 与 `#manual-scan-button` 既有触发契约以兼容冒烟脚本。
3. **毛玻璃粘性顶栏**：`.topbar` 改为半透明材质层（`backdrop-filter: blur(18px) saturate(160%)`，在 `prefers-reduced-transparency: reduce` 下回退不透明 `--bg`），左侧展示 `设备名 / 当前视图` 面包屑，中部放置命令搜索入口（CD-06 激活），右侧保留 `#library-read-banner` 与 `#settings-button`（不新增顶栏 `#theme-toggle-btn`，保持设置弹窗 `#preference-theme` 唯一入口）。

**涉及文件**
- `src/templates/plcn.html`、`src/templates/workbench.js`、`src/templates/library-navigation.js`、`src/templates/game-files.js`、`src/templates/workbench.css`、`src/templates/ui.js`
- `tests/python/test_ui_information_architecture.py`、`tests/browser_smoke.cjs`

**验收标准**
- [ ] 切换 `全部存储 / 内部存储 / 存储卡` 时，硬件坞容量条高亮对应分段时间即时联动总览与列表数量。
- [ ] 在「库总览 ↔ 主机列表 ↔ 游戏文件」之间往返切换时，左侧栏宽度、品牌区与硬件坞位置完全固定，无整屏跳动；详情页「在游戏文件中查看」定位并选中目标文件功能回归通过。

---

### CD-03 · 库总览收藏舱：健康遥测面板、氛围扇形展台与精密工具坞

**现状问题**
- 总览页顶部仅有单行 `.overview-subtitle` 文本，核心资产健康度（盒封完整率、待中文化数）感知弱。
- 「光盘 CHD 转换」与「目录整理与 ROM 压缩」藏在侧栏底部 `<details class="tools-menu">` 上拉菜单内，发现成本高。

**实施内容**
1. **首屏双栏 Bento 看板**：
   - 左栏为「收藏库遥测面板」：展示总收录游戏数、主机平台数、盒封完整率百分比及待补全项统计；存在缺封面或非中文名时，在面板显著位置渲染 `#overview-status` 与 `#overview-complete-button`（调用既有 `startCompletion()`）。
   - 右栏为「存储与多盘精密工具坞」：平铺展示「光盘 CHD 转换」（调用 `openStorageOptimization()`）与「目录整理与 ROM 压缩」（调用 `openRomStorage()`）功能行，附简明用途副标题；侧栏底部 `.tools-menu` 保留作为全局兜底入口。
2. **主机收藏匣卡片精修**：
   - 保留 `renderPlatformFan` 的真实图片比例采样与三图扇形排布逻辑，为 `.platform-cover` 增加实体包装盒高光边线与细腻投影；悬停时扇形角度微幅舒展（`prefers-reduced-motion: no-preference` 下生效）。
   - 按主机缩写（`SS`、`SNES`、`GBA`、`PS1`、`NES`、`MD`、`FBNeo`、`MAME`）映射克制的顶部径向氛围背光（不透明度控制在 `12%–16%`，严禁高饱和满铺底色），卡片底栏直接展示该主机封面完整度状态（如 `盒封 100% 完整` 或 `N 款缺少封面`）。

**涉及文件**
- `src/templates/workbench.js`、`src/templates/workbench.css`、`src/templates/ui.js`
- `tests/python/test_ui_information_architecture.py`、`tests/browser_smoke.cjs`

**验收标准**
- [ ] 空库态仍准确显示引导文案「还没有游戏。打开「游戏文件」开始。」；读取错误态保留 `#overview-note` 诊断信息。
- [ ] 1440×900 与 1100×700 视口下 Bento 看板与主机网格自适应折行，无长英文主机名溢出。

---

### CD-04 · 盒封画廊精修与「非破坏式右侧滑出检视抽屉」

**现状问题**
- `src/templates/game-browser.js` 的 `renderGameInspector` 通过 `.game-details-page .game-library-main { display: none }` 隐藏整个封面网格与搜索栏，导致用户在连续浏览、核对多款游戏资料或批量改名时频繁迷失上下文。
- 封面墙上无法一眼区分单文件卡带、已压缩 `CHD` 光盘与多盘 `M3U` 索引。

**实施内容**
1. **宽屏双栏并存抽屉（Slide-Over Inspector）**：
   - 在视口宽度 `≥ 1100px` 时，打开 `#game-inspector` 不再隐藏 `.panel-header` 与 `.game-library-main`；`.game-library` 切换为 `grid-template-columns: minmax(0, 1fr) 348px`，右侧检视面板固定粘性高度独立滚动，左侧封面墙保持原滚动位置并允许直接点击切换下一款被检视游戏（被检视项以 `.inspected` 钴蓝外框高亮）。
   - 在视口宽度 `< 1100px` 时，保留 `.game-details-page` 单栏全宽详情视图及 `gameDetailsReturnTop` 滚动位置恢复逻辑，兼顾紧凑窗口可用性。
   - 当用户在检视抽屉内点开封面替换（`artworkDialogState.docked`）时，保持抽屉内停靠交互流畅且不裁切底部确认按钮。
2. **盒封卡片精修**：
   - 保留 `wbCoverFrameRatio(system)` 自适应比例与左上角悬停/选中态显示的 `.game-card-select` 复选框；
   - 从 `item.path` / `item.rom_name` 解析扩展名（如 `CHD`、`M3U`、`ZIP`、`SFC`、`GBA`），在 `.game-card-art` 右下角叠加磨砂半透明等宽微标签（设置 `aria-hidden="true"`，不改变 `.game-card-open` 的 `aria-label`）。
3. **详情抽屉内部对齐 [GAME_DETAILS_UX_REFINEMENT.md](GAME_DETAILS_UX_REFINEMENT.md)**：
   - 封面框右上角贴边放置图片管理按钮；标题行紧凑排列游戏名、`#inspector-edit-name` 铅笔与靠右的 `#inspector-actions-menu`「更多操作」；
   - 主字段展示「游戏占用」（复合光盘取 `details.total_size`）、合并后的「运行核心」状态，以及紧邻 `#inspector-copy-path` 与「在游戏文件中查看」的等宽启动路径框。

**涉及文件**
- `src/templates/game-browser.js`、`src/templates/artwork.js`、`src/templates/workbench.css`
- `tests/python/test_ui_information_architecture.py`（同步更新 `test_system_page_uses_minimal_concept` 中 `.game-details-page .game-library-main` 的断点作用域断言）
- `tests/browser_smoke.cjs`（更新桌面视口打开详情后返回/切换条目的可见性断言）

**验收标准**
- [ ] `1440×900` 下点击游戏 A 打开右侧抽屉，滚动左侧网格再点击游戏 B，抽屉内容与顶部主机氛围光平滑切换至游戏 B，左侧滚动条不归零；按关闭按钮 `#inspector-close` 后焦点准确回到当前游戏卡片。
- [ ] `780×900` 紧凑窗口下自动采用全宽详情模式，关闭后恢复原滚动高度。
- [ ] 勾选多款游戏时，`.game-selection-toolbar` 浮动选择栏在封面视图与列表视图间完全同步。

---

### CD-05 · 一键补全「可视化对照校对台（Visual Diff Workbench）」

**现状问题**
- `src/templates/preview.js` 将缩略图、新名称输入框、ROM 文件名全部纵向堆叠在同一单元格，五列表格信息拥挤，缺乏「修改前原名 → 修改后中文名 + 盒封预览」的直观对照感。

**实施内容**
1. **直观前后对照布局**：在完全保留 `currentChanges` 数据结构、`filter-btn` 八态筛选计数、`#preview-options`、`#apply-summary-panel` 与 `applyChanges()` 提交契约的前提下，优化 `#preview-table` 列结构与视觉层次：
   - **勾选列**（`.select-cell`）→ **原始条目列**（展示现有列表标签 + 等宽 ROM 文件名）→ **指向符** → **目标中文译名与身份证据列**（内嵌既有 `buildPreviewEditor` 就地编辑与保存为本地修正规则入口）→ **盒封缩略图列**（`44×58` 实体盒封小图，点击直接调用 `openArtworkDialog` 更换封面源）→ **修复状态胶囊列**。
2. **风险分级视觉引导**：
   - `matched`（明确匹配可应用）行采用清爽表面底色 + 薄荷绿状态胶囊；
   - `review`（待补充）与 `duplicate`（重复需处理）行采用柔和琥珀色描边与状态胶囊（禁用左侧侧边粗色条，改用整行 1px 语义边框 + 胶囊），使需要人工介入的少数条目在百行列表中一眼跳出。

**涉及文件**
- `src/templates/plcn.html`、`src/templates/preview.js`、`src/templates/plcn.css`、`src/templates/workbench.css`
- `tests/ui_workflow.test.cjs`、`tests/python/test_ui_workflow_behavior.py`、`tests/browser_smoke.cjs`

**验收标准**
- [ ] 预览筛选、搜索、手动编辑名称并保存本地修正规则（`/api/overrides/save`）、单项更换封面、确认应用及写回后状态刷新全部回归通过。
- [ ] `tests/python/test_ui_information_architecture.py` 中关于预览表文案与状态标签的断言全部满足。

---

### CD-06 · 全局 `⌘K` 快捷命令面板与访达级文件工作台融合

**现状问题**
- 当掌机包含十余个主机分类及多种存储工具时，纯鼠标逐层点击路径较长，缺少现代桌面效率工具标配的键盘快速跳转面板。
- `#game-files-workspace` 的外框、表头与文件行样式偏朴素，尚未融入新的阳极氧化铝面板体系。

**实施内容**
1. **全局命令面板（`#command-palette-dialog`）**：
   - 点击顶栏命令搜索框或按下 `⌘K` / `Ctrl+K` 打开模态命令面板（打开后显式聚焦搜索输入框，支持 `↑`/`↓` 导航、`Enter` 执行、`Esc` 关闭并归还焦点）。
   - 纯前端实时检索并执行：跳转任意已扫描主机列表、进入「库总览 / 游戏文件」、打开「光盘 CHD 转换 / 目录整理与 ROM 压缩 / 自动扫描 / 手动添加游戏 / 设置」。
   - 当 `workbench.operation` 非空或正在读取库（`libraryRead.blocking`）时，自动过滤或禁用需要空闲锁的操作项，严防并发冲突。
2. **访达级文件工作台视觉对齐**：
   - 将 `#game-files-workspace` 升级为带内部分栏的双区容器（左栏存储卷导航、右栏可点击面包屑工具栏 + 排序表头 + 文件行 + 底部单行状态栏 `.game-files-status`）。
   - 为文件行补充只读类型/大小等宽辅助展示（目录显示「文件夹」，文件按后缀显示 `CHD` / `M3U` / `ROM` 等），新增高亮 `.game-files-new` 采用柔和薄荷绿底色过渡。
   - **零行为变更约束**：完整保留单击进入文件夹、行首复选框与 Shift/Cmd 多选、行末「更多操作」菜单、Finder 原生拖入、`game-file-overwrite-dialog` 无默认选中按钮确认框、`runOpsJob` 替换进度及 C-03n 四格并发结算逻辑。

**涉及文件**
- `src/templates/plcn.html`、`src/templates/workbench.js`、`src/templates/game-files.js`、`src/templates/workbench.css`、`src/templates/ui.js`
- `tests/game_files_state.test.cjs`、`tests/browser_smoke.cjs`

**验收标准**
- [ ] 按 `⌘K` 输入 `SS` 或 `CHD` 可在 2 次按键内直达对应主机或工具弹窗；模态弹窗已打开或任务执行中不误触冲突命令。
- [ ] `tests/browser_smoke.cjs` 中全部游戏文件用例（含 C-03a/b 有效批次串行写入、C-03h 错误行追加摘要、NavM/N/P/Q 四格矩阵、同名替换/跳过/取消）100% 通过。

---

## 三、每批次通用回归与交付清单

每完成一个工单（`CD-01` 至 `CD-06`），提交前必须按 [AGENTS.md](../AGENTS.md) 依次执行以下门禁：

```sh
# 1. 清理 ExFAT 卷生成的 AppleDouble 文件
find . -name '._*' -delete

# 2. 校验前端脚本语法、加载顺序与内联事件处理器
python3 scripts/check_ui_js.py

# 3. 运行 Node 状态与组件单元测试
for f in tests/*.test.cjs; do node "$f" || exit 1; done

# 4. 运行 UI 信息架构、双语覆盖与全量 Python + 真 Chrome 冒烟回归
python3 -m pytest

# 5. 检查空白字符与 diff 卫生
git diff --check
```

### 桌面端打包与真机视觉复核（里程碑节点执行）

- 使用 `~/.venvs/plcn-build/bin/python scripts/package_desktop.py --arch arm64` 构建，清理 `dist` 内 `._*` 后重签，并通过 `ditto`（勿用 `cp -R`）同步至 `/Applications/RetroArch Assistant.app` 与 DMG staging。
- 在 **1440×900（宽屏双栏）** 与 **1100×700 / 780×900（紧凑视口）**、**简体中文 / English**、**深色阳极氧化铝 / 浅色精密陶瓷银** 共 8 组组合下截图归档，逐一核对对比度、焦点环、长文件名截断与无默认选中按钮行为。
