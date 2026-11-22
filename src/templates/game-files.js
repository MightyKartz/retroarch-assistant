/* File browsing works independently of the selected RetroArch library. */
let gameFilesView = null;
let gameFilesClipboard = null;
let fileChanges = {root: '', items: []};

function recordFileChange(root, entry) {
    if (fileChanges.root !== root) fileChanges = {root, items: []};
    fileChanges.items.push(entry);
    renderFileSyncNotice();
}

function dismissFileChanges() {
    fileChanges = {root: fileChanges.root, items: []};
    renderFileSyncNotice();
}

function renderFileSyncNotice() {
    const notice = document.getElementById('game-sync-notice');
    if (!notice) return;
    const root = document.getElementById('retroarch_root')?.value || '';
    const count = fileChanges.root === root ? fileChanges.items.length : 0;
    notice.hidden = !count;
    if (!count) { notice.replaceChildren(); return; }
    notice.replaceChildren(
        wbElement('span', '', workbenchText(`文件管理中有 ${count} 项改动未同步到列表。`, `${count} file change(s) from the file manager are not synced to playlists.`)),
        gmButton('更新列表…', 'Update playlists…', openFileSyncDialog, false),
        gmButton('忽略', 'Dismiss', dismissFileChanges));
}

async function openFileSyncDialog() {
    const items = fileChanges.items, root = fileChanges.root;
    if (!items.length || workbench.operation) return;
    if (!beginWorkbenchOperation('game-file-sync')) {
        showStatus(workbenchText('请等待当前任务完成。', 'Wait for the current task.'), 'error');
        return;
    }
    const tr = workbenchText;
    const dialog = wbElement('dialog', 'desktop-confirm app-dialog game-file-sync-dialog');
    const heading = wbElement('h2', '', tr('同步文件改动到列表', 'Sync file changes to playlists'));
    heading.id = 'game-file-sync-title';
    heading.setAttribute('autofocus', '');
    dialog.setAttribute('aria-labelledby', heading.id);
    const impact = wbElement('div', 'game-file-delete-impact');
    const message = wbElement('p', '', '');
    message.setAttribute('role', 'status');
    const actions = wbElement('div', 'desktop-confirm-actions');
    const cancel = gmButton('取消', 'Cancel', () => dialog.close());
    const confirm = gmButton('更新列表', 'Update playlists', apply, false);
    confirm.disabled = true;
    let previewId;
    actions.append(cancel, confirm);
    dialog.append(heading, impact, message, actions);
    document.body.append(dialog);
    dialog.addEventListener('close', () => { endWorkbenchOperation(); dialog.remove(); }, {once:true});
    dialog.showModal();
    runPreview();

    function list(zh, en, entries) {
        if (!entries.length) return;
        const rows = wbElement('ul', '');
        rows.append(...entries.map(entry => wbElement('li', '', entry)));
        impact.append(wbElement('h3', '', tr(zh, en)), rows);
    }

    async function runPreview() {
        try {
            const {id, result} = await runGameJob('/api/game-files/sync-preview', {root, changes: items},
                state => { if (state.connectionError || currentUILanguage !== 'en') message.textContent = state.message; });
            previewId = id;
            list('将改写的列表条目', 'Playlist entries to update',
                 result.renamed.map(entry => entry.playlist + ' · ' + entry.label + ' → ' + entry.new));
            list('将移出的列表条目', 'Playlist entries to remove',
                 result.removed.map(entry => entry.playlist + ' · ' + entry.label));
            list('未匹配到条目的改动', 'Changes with no matching entry', result.unmatched);
            list('无法读取的列表', 'Unreadable playlists',
                 (result.failed || []).map(entry => entry.playlist + ' · ' + entry.error));
            const total = result.renamed.length + result.removed.length;
            const failures = (result.failed || []).length;
            const allFailed = failures && failures === result.playlists_total;
            message.textContent = total
                ? tr(`将更新 ${result.renamed.length} 条、移出 ${result.removed.length} 条列表条目。` +
                     (failures ? `${failures} 个列表读取失败，将保持原样。` : ''),
                     `${result.renamed.length} entries will be updated and ${result.removed.length} removed.` +
                     (failures ? ` ${failures} playlist(s) unreadable and left unchanged.` : ''))
                : allFailed
                    ? tr('所有列表都无法读取，请修复后再同步。', 'Every playlist failed to read; fix them before syncing.')
                    : failures
                        ? tr(`没有需要更新的列表条目；${failures} 个列表读取失败，将保持原样。`,
                             `No playlist entries need updating; ${failures} playlist(s) unreadable and left unchanged.`)
                        : tr('没有需要更新的列表条目。', 'No playlist entries need updating.');
            confirm.disabled = !total;
        } catch (error) {
            message.textContent = gmError(error);
        }
    }

    async function apply() {
        if (!previewId) return;
        confirm.disabled = cancel.disabled = true;
        try {
            const {result} = await runGameJob('/api/game-files/sync-apply', {preview_job_id: previewId},
                state => { if (state.connectionError || currentUILanguage !== 'en') message.textContent = state.message; });
            await refreshDeletedPlaylists(result.playlists);
            dismissFileChanges();
            dialog.close();
        } catch (error) {
            message.textContent = gmError(error);
            confirm.disabled = cancel.disabled = false;
        }
    }
}

function closeGameFiles() {
    if (!gameFilesView) return;
    const {shell, workspace, sidebar, stopLayout, opener} = gameFilesView;
    stopLayout();
    gameFilesView = null;
    shell.classList.remove('showing-game-files');
    workspace.hidden = sidebar.hidden = true;
    workspace.replaceChildren();
    sidebar.replaceChildren();
    opener.focus();
}

function openGameFiles(opener, target = null) {
    if (workbench.operation || gameFilesView) return;

    const tr = workbenchText;
    const shell = document.querySelector('.app-shell');
    const workspace = document.getElementById('game-files-workspace');
    const sidebar = document.getElementById('game-files-sidebar');
    const mobileNav = wbElement('div', 'game-files-mobile-nav');
    const view = {shell, workspace, sidebar, opener};
    gameFilesView = view;
    const root = document.getElementById('retroarch_root').value;
    const system = document.getElementById('system_name').value;
    const header = wbElement('header', 'game-files-header');
    const title = wbElement('h2', '', tr('游戏文件', 'Game files'));
    title.id = 'game-files-title';
    title.tabIndex = -1;
    const back = gmButton('返回游戏库', 'Back to library', closeGameFiles);
    wbIconButton(back, 'arrow-left', back.textContent);
    header.append(title, back);

    const navigation = wbElement('nav', 'game-files-navigation');
    navigation.setAttribute('aria-label', tr('存储位置', 'Storage locations'));
    const remote = wbElement('div', 'game-files-storages');
    const storageStatus = wbElement('p', 'game-files-muted');
    storageStatus.setAttribute('role', 'status');
    const refresh = gmButton('重新检测存储', 'Detect storage again', loadStorages);
    const local = gmButton('电脑目录', 'Computer folder', chooseFolder);
    local.id = 'game-files-local';
    local.prepend(wbIcon('folder'));
    navigation.append(wbElement('h3', '', tr('电脑', 'Computer')), local,
        wbElement('h3', '', tr('USB 掌机', 'USB handheld')), remote, storageStatus, refresh);

    const pane = wbElement('section', 'game-files-pane');
    const toolbar = wbElement('div', 'game-files-toolbar');
    const up = gmButton('上级目录', 'Parent folder', () => loadDirectory(current.parent));
    wbIconButton(up, 'arrow-up', up.textContent);
    up.disabled = true;
    const location = wbElement('nav', 'game-files-location', tr('选择要浏览的存储或电脑目录', 'Choose a storage or computer folder'));
    location.setAttribute('aria-label', tr('当前路径', 'Current path'));
    const selectionCount = wbElement('span', 'game-files-selection');
    const add = gmButton('添加游戏', 'Add games', addItems, false);
    add.id = 'game-files-add';
    wbIconButton(add, 'plus', tr('添加游戏', 'Add games'));
    const rename = gmButton('重命名', 'Rename', () => renameFile());
    const remove = gmButton('删除文件', 'Delete file', () => deleteFile());
    const copy = gmButton('复制', 'Copy', () => copyFile());
    const paste = gmButton('粘贴', 'Paste', pasteFiles);
    wbIconButton(rename, 'pencil', rename.textContent);
    wbIconButton(remove, 'trash', remove.textContent);
    wbIconButton(copy, 'copy', copy.textContent);
    wbIconButton(paste, 'clipboard', paste.textContent);
    rename.disabled = remove.disabled = copy.disabled = paste.disabled = add.disabled = true;
    toolbar.append(up, location, selectionCount, rename, remove, copy, paste, add);
    const table = wbElement('div', 'game-files-table');
    const rows = wbElement('ul', 'game-files-rows');
    const empty = wbElement('p', 'game-files-empty', tr('从左侧选择要存放游戏的目录', 'Pick a folder on the left to store games'));
    const sort = wbElement('button', 'game-files-sort');
    sort.type = 'button';
    sort.onclick = () => { sortDirection = sortDirection === 'asc' ? 'desc' : 'asc'; renderSortHead(); renderRows(); };
    const column = wbElement('div', 'game-files-column');
    column.append(sort);
    table.append(column, empty, rows);
    const status = wbElement('p', 'game-files-status');
    status.setAttribute('role', 'status');
    const detailBox = wbElement('details', 'game-files-detail');
    detailBox.hidden = true;
    pane.append(toolbar, table, status, detailBox);
    sidebar.append(navigation);
    workspace.append(header, mobileNav, pane);
    shell.classList.add('showing-game-files');
    workspace.hidden = sidebar.hidden = false;
    title.focus();

    const narrow = matchMedia('(max-width:560px)');
    const placeNavigation = () => (narrow.matches ? mobileNav : sidebar).append(navigation);
    placeNavigation();
    narrow.addEventListener('change', placeNavigation);
    view.stopLayout = () => narrow.removeEventListener('change', placeNavigation);

    let current = null;
    let selection = new Set();
    let anchor = null;
    let changing = false;
    let lastLocal = '';
    let directoryRequest = 0;
    let directoryLoading = false;
    let navVersion = 0;
    /* User navigation outranks automatic refreshes from the moment its
       request is issued: a nav response applies whenever it is still the
       latest navigation, while a refresh applies only when no navigation
       is pending and none landed after the refresh was issued. */
    let latestNav = 0;
    let navInFlight = false;
    let navApplied = 0;
    let afterNavRefresh = null;
    /* Counts every workbench write issued by this page — rename, delete,
       paste and import. A preview sampled before a write must not be
       trusted when it lands after the write finished; a changed version
       sends the batch back for a fresh sample. */
    let writeVersion = 0;
    let lastReadError = 0;   // request id whose read error the status line currently shows
    let lastOpsDetail = null;   // what renderOpsDetail currently shows, for appending notices
    let expiredNotice = 0;      // in-flight nav request id that owns a pending drop-expired notice
    let statusSummary = null;   // write summary the status line currently presents
    let sortDirection = 'asc';
    let highlights = new Set();
    let dropZone = null;
    /* Matched drops are queued and processed one at a time: each batch keeps
       its own target directory and navigation context, so a batch delivered
       while an earlier import is still writing waits instead of silently
       dropping out. */
    const batchQueue = [];
    let batchRunning = false;
    /* Captures pushed by the native side, keyed by the drop's own nonce, and
       confirmed landing zones keyed the same way. A nonce can only be read
       out of this drop's dataTransfer, so a source is bound to the exact
       gesture it arrived with — never to a time window — and each matched
       record is consumed by its own event only. */
    const pendingSources = new Map();
    const pendingDrops = new Map();
    view.refresh = () => { if (current) loadDirectory(current.path); };
    view.externalDrop = externalDrop;
    view.dropSource = dropSource;
    loadStorages();
    if (target) loadDirectory(target.directory, target.path);
    else if (root && !root.startsWith('adb://')) {
        const initialRequest = directoryRequest;
        desktopRequest('/api/content/setup?root=' + encodeURIComponent(root)).then(({profiles}) => {
            if (gameFilesView !== view || initialRequest !== directoryRequest) return;
            const selected = document.getElementById('playlist_path').value;
            const filename = selected.split(/[\\/]/).pop();
            const name = selected.startsWith('mtp://') ? decodeURIComponent(filename) : filename;
            const directories = new Set(profiles.filter(profile => profile.system === system &&
                (!name || profile.playlist_name === name)).map(profile => profile.directory));
            if (directories.size === 1) loadDirectory([...directories][0]);
        }).catch(error => {
            if (gameFilesView === view && initialRequest === directoryRequest) status.textContent = error.message;
        });
    }

    function updateActionState() {
        rename.disabled = selection.size !== 1 || changing;
        remove.disabled = copy.disabled = !selection.size || changing;
        paste.disabled = !gameFilesClipboard?.items?.length || gameFilesClipboard.root !== root || !current?.path || changing;
        const mtpDest = current?.path?.startsWith('mtp://');
        const blocked = Boolean(mtpDest && (!root.startsWith('mtp://') || root.split('/')[2] !== current.path.split('/')[2]));
        add.disabled = !current?.path || changing || blocked;
        add.title = blocked
            ? tr('先在游戏库中打开这台掌机。', 'Open this handheld library first.')
            : tr('把电脑上的文件或文件夹复制进当前目录。', 'Copy computer files or folders into this folder.');
        empty.hidden = Boolean(current);
    }

    function renderSortHead() {
        const asc = sortDirection === 'asc';
        sort.replaceChildren(tr('名称', 'Name'), wbIcon(asc ? 'arrow-up' : 'arrow-down'));
        sort.setAttribute('aria-label', tr(asc ? '名称：当前升序，激活切换为降序' : '名称：当前降序，激活切换为升序',
            asc ? 'Name: ascending, activate to sort descending' : 'Name: descending, activate to sort ascending'));
    }
    renderSortHead();

    function displayItems() {
        return gameFilesSorted(current?.items || [], sortDirection);
    }

    function selectionItems() {
        const byPath = new Map((current?.items || []).map(item => [item.path, item]));
        return [...selection].map(path => byPath.get(path)).filter(Boolean);
    }

    function toggleItem(path, on = !selection.has(path)) {
        if (on) selection.add(path);
        else selection.delete(path);
    }

    function selectRange(path) {
        const order = displayItems().map(item => item.path);
        const from = order.indexOf(anchor), to = order.indexOf(path);
        if (from < 0 || to < 0) {
            selection = new Set([path]);
            anchor = path;
        } else {
            selection = new Set(order.slice(Math.min(from, to), Math.max(from, to) + 1));
        }
        syncSelectionUI();
    }

    function pickItem(item, event) {
        if (event.shiftKey && anchor) { selectRange(item.path); return; }
        if (event.metaKey || event.ctrlKey) toggleItem(item.path);
        else selection = new Set([item.path]);
        anchor = item.path;
        syncSelectionUI();
    }

    function clearSelection() {
        selection = new Set();
        anchor = null;
        syncSelectionUI();
    }

    function syncSelectionUI() {
        for (const row of rows.children) {
            const on = selection.has(row.dataset.path);
            row.classList.toggle('game-files-selected', on);
            const button = row.querySelector('.btn[aria-pressed]');
            if (button) button.setAttribute('aria-pressed', on ? 'true' : 'false');
            const box = row.querySelector('.game-files-check');
            if (box) box.checked = on;
        }
        selectionCount.textContent = selection.size
            ? tr(`已选 ${selection.size} 项`, `${selection.size} selected`) : '';
        updateActionState();
    }

    pane.addEventListener('keydown', event => {
        if (event.key === 'Escape' && selection.size && !event.target.closest('details')) clearSelection();
    });

    function renderRows(scrollToPath = null) {
        rows.replaceChildren(...displayItems().map(item => {
            const row = wbElement('li', 'game-files-row');
            row.dataset.path = item.path;
            row.dataset.folder = item.directory ? 'true' : 'false';
            if (highlights.has(item.name)) row.classList.add('game-files-new');
            const box = wbElement('input', 'game-files-check');
            box.type = 'checkbox';
            box.setAttribute('aria-label', tr('选择 ' + item.name, 'Select ' + item.name));
            box.onchange = () => { toggleItem(item.path, box.checked); anchor = item.path; syncSelectionUI(); };
            const button = gmButton(item.name, item.name, event => {
                if (item.directory && !event.shiftKey && !event.metaKey && !event.ctrlKey) loadDirectory(item.path);
                else pickItem(item, event);
            });
            if (item.directory) button.prepend(wbIcon('folder'));
            else button.setAttribute('aria-pressed', 'false');
            if (item.directory) {
                const menu = wbElement('details', 'action-menu game-files-row-menu');
                const summary = wbElement('summary', 'action-menu-trigger');
                summary.setAttribute('aria-label', tr(item.name + ' 的更多操作', 'More actions for ' + item.name));
                summary.append(wbIcon('dots'));
                const drop = wbElement('div', 'action-menu-list');
                const entry = (zh, en, action) => {
                    const option = wbElement('button', 'action-menu-item', tr(zh, en));
                    option.onclick = () => { menu.open = false; action(item); };
                    drop.append(option);
                };
                entry('重命名', 'Rename', renameFile);
                entry('复制', 'Copy', copyFile);
                entry('删除…', 'Delete…', deleteFile);
                menu.append(summary, drop);
                row.append(box, button, menu);
            } else {
                row.append(box, button);
            }
            return row;
        }));
        syncSelectionUI();
        if (scrollToPath) {
            const targetRow = [...rows.children].find(row => row.dataset.path === scrollToPath);
            targetRow?.scrollIntoView({block:'nearest'});
        }
    }

    function clearDrop() {
        dropZone = null;
        table.classList.remove('game-files-drop');
        for (const row of rows.children) row.classList.remove('game-files-drop-target');
    }
    table.addEventListener('dragover', event => {
        event.preventDefault();
        if (directoryLoading) { clearDrop(); return; }
        const row = event.target?.closest ? event.target.closest('.game-files-row') : null;
        const folderPath = row && row.dataset.folder === 'true' ? row.dataset.path : '';
        dropZone = {directory: folderPath || current?.path || '', context: current?.path || '',
                    at: Date.now(), nav: navVersion};
        table.classList.add('game-files-drop');
        for (const entry of rows.children) entry.classList.toggle('game-files-drop-target', entry === row && Boolean(folderPath));
    });
    table.addEventListener('dragleave', event => {
        if (!table.contains(event.relatedTarget)) clearDrop();
    });
    function readDropNonce(dt) {
        try { const value = dt?.getData?.('org.plcn.dropid'); if (value) return value; } catch (error) {}
        const types = dt?.types ? Array.from(dt.types) : [];
        const hit = types.find(type => type.startsWith('org.plcn.dropid.'));
        return hit ? hit.slice('org.plcn.dropid.'.length) : null;
    }

    table.addEventListener('drop', event => {
        event.preventDefault();
        const zone = dropZone;
        if (zone) {
            zone.at = Date.now(); zone.dropped = true; zone.stamp = event.timeStamp;
            zone.nonce = readDropNonce(event.dataTransfer);
        }
        if (zone?.nonce) {
            pendingDrops.set(zone.nonce, zone);
            if (pendingDrops.size > 8) pendingDrops.delete(pendingDrops.keys().next().value);
            const source = pendingSources.get(zone.nonce);
            if (source) {
                pendingSources.delete(zone.nonce);
                pendingDrops.delete(zone.nonce);
                acceptPaths(zone, source.paths);
                return;
            }
        }
        if (event.dataTransfer?.files?.length && zone) {
            setTimeout(() => {
                const pending = zone.nonce ? pendingDrops.get(zone.nonce) === zone : dropZone === zone;
                if (gameFilesView === view && pending) {
                    pendingDrops.delete(zone.nonce);
                    clearDrop();
                    status.textContent = tr('无法取得拖入文件的完整路径，请使用「添加游戏」。',
                        'Dropped file paths are unavailable here. Use "Add games" instead.');
                }
            }, 800);
        }
    });

    function dropSource(payload) {
        const nonce = payload?.nonce;
        if (!nonce || !Array.isArray(payload.paths)) return;
        const zone = pendingDrops.get(nonce);
        if (zone) {
            pendingDrops.delete(nonce);
            acceptPaths(zone, payload.paths);
            return;
        }
        pendingSources.set(nonce, {paths: payload.paths, at: Date.now()});
        if (pendingSources.size > 8) pendingSources.delete(pendingSources.keys().next().value);
        // Sources whose drop never arrives are aged out so an old record can
        // never be claimed by a later, unrelated drop.
        setTimeout(() => {
            const source = pendingSources.get(nonce);
            if (source && source.at < Date.now() - 3000) pendingSources.delete(nonce);
        }, 3500);
    }

    /* Consume a nonce-bound drop: the zone was validated when the drop event
       landed. An in-flight write must not expire a still-valid landing — the
       busy flag only gates when the queued batch executes, not whether its
       own event and context are real. */
    function acceptPaths(zone, paths) {
        if (zone === dropZone) clearDrop();
        if (!paths?.length) return;
        if (paths.some(path => typeof path !== 'string' || !path)) {
            status.textContent = tr('无法确认拖入内容的完整路径，请改用「添加游戏」。',
                'Could not confirm a dropped item’s full path. Use "Add games" instead.');
            return;
        }
        const directory = gameFilesDropDirectory(zone, current, navVersion, false, zone.stamp);
        if (!directory) {
            showDropExpired();
            return;
        }
        batchQueue.push({paths, directory, context: zone.context, nav: zone.nav});
        runBatches();
    }

    /* The workbench mutex is shared with manual paste, delete and the
       import write itself — a queued drop waits for it rather than dying
       on it. `workbench` lives in workflow.js; guard it for standalone
       harnesses that load only this file. */
    function workbenchBusy() {
        return changing || (typeof workbench !== 'undefined' && Boolean(workbench.operation));
    }

    /* A finished write refreshes whatever the user is looking at — never
       drags the view back to the write target. While a user navigation is
       in flight it owns the next render; the refresh is replayed only when
       the landed view still shows the write target, and the write's summary
       plus its full operation detail are restored after navigation settles. */
    function refreshAfterWrite(directory, names = new Set(), message = '', detail = []) {
        if (navInFlight) {
            afterNavRefresh = {directory, names, message, detail, writeVersion};
            return Promise.resolve('deferred');
        }
        return loadDirectory(current?.path || directory, null, names);
    }

    async function runBatches() {
        if (batchRunning) return;
        batchRunning = true;
        try {
            while (batchQueue.length && gameFilesView === view) {
                const batch = batchQueue[0];
                const expired = batch.nav !== navVersion || !current || batch.context !== current.path
                    || (batch.directory !== current.path
                        && !current.items.some(item => item.directory && item.path === batch.directory));
                if (expired) {
                    batchQueue.shift();
                    showDropExpired();
                    continue;
                }
                if (workbenchBusy()) { await new Promise(resolve => setTimeout(resolve, 400)); continue; }
                batchQueue.shift();
                await confirmAdd(batch.paths, batch.directory, batch);
            }
        } finally {
            batchRunning = false;
        }
    }

    function externalDrop(paths, stamp) {
        const zone = dropZone;
        // Only the drop whose event timestamp matches may consume this zone;
        // a late callback for an older drop leaves newer zones untouched.
        if (zone?.dropped && zone.stamp === stamp && stamp != null) clearDrop();
        if (!paths?.length) return;
        if (paths.some(path => typeof path !== 'string' || !path)) {
            if (zone?.dropped && zone.stamp === stamp && stamp != null) {
                status.textContent = tr('无法确认拖入内容的完整路径，请改用「添加游戏」。',
                    'Could not confirm a dropped item’s full path. Use "Add games" instead.');
            }
            return;
        }
        const directory = gameFilesDropDirectory(zone, current, navVersion, changing || directoryLoading, stamp);
        if (!directory) {
            if (zone?.dropped && zone.stamp === stamp && stamp != null)
                showDropExpired();
            return;
        }
        confirmAdd(paths, directory);
    }

    async function loadStorages() {
        refresh.disabled = true;
        storageStatus.textContent = tr('正在检测存储…', 'Detecting storage…');
        try {
            const result = await desktopRequest('/api/game-files/storages');
            if (gameFilesView !== view) return;
            lastLocal ||= result.local;
            remote.replaceChildren(...result.storages.map(storage => {
                const label = storage.label;
                const button = gmButton(label, label, () => loadDirectory(storage.path));
                button.dataset.storage = storage.path;
                button.prepend(wbIcon('device-sd-card'));
                if (current?.path.startsWith(storage.path)) button.setAttribute('aria-current', 'true');
                return button;
            }));
            storageStatus.textContent = result.storage_error ||
                (result.storages.length ? '' : tr('未检测到 USB 文件传输存储。', 'No USB file transfer storage detected.'));
        } catch (error) {
            storageStatus.textContent = error.message;
        } finally {
            refresh.disabled = false;
        }
    }

    async function chooseFolder() {
        local.disabled = true;
        try {
            const result = await desktopRequest('/api/desktop/pick', {kind:'directory', initial:lastLocal});
            if (gameFilesView === view && result.path) await loadDirectory(result.path);
        } catch (error) {
            status.textContent = error.message;
        } finally {
            local.disabled = false;
        }
    }

    function renameFile(item = null) {
        item = item || (selection.size === 1 ? selectionItems()[0] : null);
        if (!item || changing) return;
        const directory = current.path;
        const dialog = wbElement('dialog', 'desktop-confirm app-dialog');
        const heading = wbElement('h2', '', tr('重命名', 'Rename'));
        heading.id = 'game-file-change-title';
        dialog.setAttribute('aria-labelledby', heading.id);
        dialog.append(heading, wbElement('p', '', item.name));
        const input = wbElement('input', '');
        input.value = item.name;
        input.setAttribute('aria-label', tr('文件名', 'Filename'));
        dialog.append(input);
        const error = wbElement('p', '', '');
        error.setAttribute('role', 'alert');
        const actions = wbElement('div', 'desktop-confirm-actions');
        const cancel = gmButton('取消', 'Cancel', () => dialog.close());
        const confirm = gmButton('确认', 'Confirm', submit, false);
        actions.append(cancel, confirm);
        dialog.append(error, actions);
        document.body.append(dialog);
        dialog.addEventListener('close', () => dialog.remove(), {once:true});
        dialog.addEventListener('cancel', event => { if (changing) event.preventDefault(); });
        dialog.showModal();
        input.focus();
        input.setSelectionRange(0, Math.max(0, item.name.lastIndexOf('.')));
        input.onkeydown = event => { if (event.key === 'Enter' && !event.isComposing) submit(); };

        async function submit() {
            if (changing) return;
            changing = true; writeVersion += 1;
            cancel.disabled = confirm.disabled = true;
            try {
                const result = await desktopRequest('/api/game-files/file-ops', {action:'rename', path:item.path, root, name:input.value.trim()});
                recordFileChange(root, {action:'rename', path:item.path, to:result.new_path, folder:Boolean(item.directory)});
                dialog.close();
                if (gameFilesView === view) await refreshAfterWrite(directory);
            } catch (failure) {
                error.textContent = gmError(failure);
            } finally {
                changing = false;
                cancel.disabled = confirm.disabled = false;
            }
        }
    }

    async function runOpsJob(title, payload) {
        const dialog = wbElement('dialog', 'desktop-confirm app-dialog game-file-progress-dialog');
        const heading = wbElement('h2', '', title);
        heading.id = 'game-file-progress-title';
        heading.setAttribute('autofocus', '');
        dialog.setAttribute('aria-labelledby', heading.id);
        const track = wbElement('div', 'game-file-progress-track indeterminate');
        const fill = wbElement('div', 'game-file-progress-fill');
        track.append(fill);
        const detail = wbElement('p', 'game-file-progress-detail', tr('正在准备…', 'Preparing…'));
        const actions = wbElement('div', 'desktop-confirm-actions');
        let jobId;
        const cancelButton = gmButton('取消', 'Cancel', requestCancel);
        actions.append(cancelButton);
        dialog.append(heading, track, detail, actions);
        document.body.append(dialog);
        dialog.addEventListener('cancel', event => { event.preventDefault(); requestCancel(); });
        dialog.addEventListener('close', () => dialog.remove(), {once:true});
        dialog.showModal();
        try {
            const {result} = await runGameJob('/api/game-files/file-ops', payload, job => {
                jobId = job.id || gameManagement.job;
                const total = job.total || 0, done = job.progress || 0;
                track.classList.toggle('indeterminate', !total);
                fill.style.width = total ? Math.round(done / total * 100) + '%' : '';
                detail.textContent = jobDetail(job) + (total ? ` ${done}/${total}` : '');
            });
            return result;
        } finally {
            dialog.close();
        }

        function requestCancel() {
            if (!jobId || cancelButton.disabled) return;
            cancelButton.disabled = true;
            detail.textContent = tr('正在取消…', 'Cancelling…');
            desktopRequest('/api/jobs/cancel', {job_id: jobId}).catch(() => {});
        }
    }

    function jobDetail(job) {
        if (job.connectionError) return job.message;
        const message = job.message || '';
        if (currentUILanguage === 'en') return message.split('：').pop();
        return message;
    }

    function deleteFile(item = null) {
        const items = item ? [item] : selectionItems();
        if (!items.length || changing) return;
        const directory = current.path;
        const dialog = wbElement('dialog', 'desktop-confirm app-dialog game-file-delete-dialog');
        const heading = wbElement('h2', '', items.length === 1
            ? tr('删除文件', 'Delete item') : tr(`删除 ${items.length} 项`, `Delete ${items.length} items`));
        heading.id = 'game-file-delete-title';
        heading.setAttribute('autofocus', '');
        dialog.setAttribute('aria-labelledby', heading.id);
        const message = wbElement('p', '', '');
        message.setAttribute('role', 'status');
        const actions = wbElement('div', 'desktop-confirm-actions');
        const cancel = gmButton('取消', 'Cancel', () => dialog.close());
        const confirm = gmButton('确认', 'Confirm', apply, false);
        actions.append(cancel, confirm);
        const names = wbElement('ul', 'game-file-delete-list');
        for (const entry of items.slice(0, 12)) names.append(wbElement('li', 'game-file-dialog-name', entry.name));
        if (items.length > 12) names.append(wbElement('li', '', tr(`…… 等 ${items.length} 项`, `… and ${items.length} items`)));
        dialog.append(heading, names,
            wbElement('p', '', items.some(entry => entry.directory)
                ? tr('所选项目及其文件夹内容将被永久删除，无法撤销。', 'The selected items and their folder contents will be permanently deleted. There is no undo.')
                : tr('所选文件将被永久删除，无法撤销。', 'The selected files will be permanently deleted. There is no undo.')),
            message, actions);
        document.body.append(dialog);
        dialog.addEventListener('close', () => dialog.remove(), {once:true});
        dialog.addEventListener('cancel', event => { if (changing) event.preventDefault(); });
        dialog.showModal();

        async function apply() {
            if (changing) return;
            if (!beginWorkbenchOperation('game-file-ops')) { message.textContent = tr('请等待当前任务完成。', 'Wait for the current task.'); return; }
            changing = true; writeVersion += 1;
            dialog.close();
            try {
                const result = await runOpsJob(tr('正在删除…', 'Deleting…'),
                        {action:'delete', paths: items.map(entry => entry.path), root})
                    .catch(error => error.result?.items ? error.result : Promise.reject(error));
                for (const entry of fileChangeRegistrations(result)) recordFileChange(root, entry);
                if (gameFilesView === view) {
                    const summary = deleteSummary(result, items);
                    const outcome = await refreshAfterWrite(directory, new Set(), summary);
                    presentWriteResult(outcome, directory, new Set(), summary, []);
                }
            } catch (error) {
                status.textContent = gmError(error);
            } finally {
                changing = false;
                updateActionState();
                endWorkbenchOperation();
            }
        }
    }

    function deleteSummary(result, items) {
        const states = {deleted: [], partial: [], failed: [], pending: []};
        for (const entry of result?.items || []) (states[entry.status] || states.failed).push(entry);
        const list = entries => {
            const names = entries.map(entry => entry.name);
            return names.length > 4 ? names.slice(0, 4).join('、') + ' 等 ' + names.length + ' 项' : names.join('、');
        };
        if (items.length === 1 && states.deleted.length === 1)
            return tr('已删除「' + items[0].name + '」。', 'Deleted "' + items[0].name + '".');
        const parts = [];
        if (states.deleted.length) parts.push(tr(`已删除 ${states.deleted.length} 项`, `Deleted ${states.deleted.length} item(s)`));
        if (states.partial.length) parts.push(tr('部分删除：', 'Partially deleted: ') + list(states.partial));
        if (states.failed.length) parts.push(tr('失败：', 'Failed: ') + list(states.failed) +
            (states.failed[0].error ? tr(`（${states.failed[0].error}）`, ` (${states.failed[0].error})`) : ''));
        if (states.pending.length) parts.push(tr(`未执行 ${states.pending.length} 项`, `${states.pending.length} item(s) not run`));
        const body = parts.length ? parts.join(tr('；', '; ')) : tr('没有删除任何内容。', 'Nothing was deleted.');
        return result?.cancelled ? tr('已取消：', 'Cancelled: ') + body : body;
    }

    function copyFile(item = null) {
        const items = item ? [item] : selectionItems();
        if (!items.length || changing) return;
        gameFilesClipboard = {root, items: items.map(entry => ({path: entry.path, name: entry.name, folder: Boolean(entry.directory)}))};
        updateActionState();
        status.textContent = items.length === 1
            ? tr('已复制「' + items[0].name + '」，切换到目标目录后粘贴。', 'Copied "' + items[0].name + '". Paste it in the destination folder.')
            : tr(`已复制 ${items.length} 项，切换到目标目录后粘贴。`, `Copied ${items.length} items. Paste them in the destination folder.`);
    }

    /* Full written-path detail for partial results — the status line keeps
       the short summary while this expandable block lists every confirmed
       path, the real target, kept conflicts and the interruption reason.
       An optional lead line carries the write summary when the status line
       has to keep a read error visible instead. */
    function renderOpsDetail(entries, lead = '') {
        lastOpsDetail = {entries, lead};
        detailBox.hidden = !entries.length && !lead;
        detailBox.replaceChildren();
        if (!entries.length && !lead) return;
        detailBox.append(wbElement('summary', '', tr('查看已写入明细', 'Show written details')));
        if (lead) detailBox.append(wbElement('p', 'game-files-muted', lead));
        for (const entry of entries) {
            const block = wbElement('div', 'game-files-detail-item');
            block.append(wbElement('p', '', entry.name
                ? `${entry.name} → ${entry.target || ''}`
                : tr(`目标：${entry.target}`, `Target: ${entry.target}`)));
            if (entry.conflict)
                block.append(wbElement('p', '', tr(`已保留同名成员：${entry.conflict}`, `Kept same-named member: ${entry.conflict}`)));
            if (entry.interrupted)
                block.append(wbElement('p', '', tr(`中断成员：${entry.interrupted}`, `Interrupted member: ${entry.interrupted}`)));
            if (entry.retained)
                block.append(wbElement('p', '', tr(`原件已保留：${entry.retained}`, `Original retained: ${entry.retained}`)));
            if (entry.error)
                block.append(wbElement('p', 'game-files-muted', tr(`中断原因：${entry.error}`, `Interrupted: ${entry.error}`)));
            const list = wbElement('ul');
            for (const path of entry.written || []) list.append(wbElement('li', '', path));
            if (list.children.length)
                block.append(wbElement('p', 'game-files-muted', tr(`已写入 ${list.children.length} 项：`, `Written (${list.children.length}):`)), list);
            detailBox.append(block);
        }
    }

    function clearPasteDetail() {
        lastOpsDetail = null;
        statusSummary = null;
        detailBox.hidden = true;
        detailBox.replaceChildren();
    }

    /* The expired-batch notice must not bury a read error that is still on
       screen: when the status line shows one, the notice joins the detail
       block so the error and the completed write's result stay visible. */
    const dropExpiredMessage = () => tr('放置位置已失效，请重新拖入。', 'The drop target expired. Drag the files in again.');

    /* The lead merge every feedback source uses — a deduped join that keeps
       whatever the detail block already leads with (write summary, earlier
       notices). */
    function detailLead(prior, addition) {
        if (!addition) return prior || '';
        if (!prior) return addition;
        return prior.includes(addition) ? prior : `${prior} ${addition}`;
    }

    /* The single presentation entry for the status line plus the detail
       block — nav settlement, write results and the drop-expiry notice all
       funnel their status/detail updates through here. `entries`+`lead`
       update the detail block (merged into the existing lead, or replacing
       it wholesale with `replace`); `headline` claims the status line only
       while no read error is showing, and `own` marks it as a write summary
       so a later notice can preserve it as the detail lead. */
    function presentResult({entries = null, lead = '', replace = false, headline = '', own = false}) {
        if (entries !== null || lead) {
            const merged = replace ? lead : detailLead(lastOpsDetail?.lead || statusSummary || '', lead);
            renderOpsDetail(entries ?? lastOpsDetail?.entries ?? [], merged);
        }
        if (headline && !lastReadError) {
            status.textContent = headline;
            if (own) statusSummary = headline;
        }
    }

    /* A presented write result or a visible read error keeps its place: the
       notice joins the detail lead instead of burying either — with no prior
       result and no error it stays a plain status message. */
    function showDropExpired() {
        const message = dropExpiredMessage();
        if (navInFlight) expiredNotice = latestNav;   // settle with that navigation's outcome
        const entries = lastOpsDetail?.entries || [];
        const prior = lastOpsDetail?.lead || statusSummary || '';
        if (!lastReadError && !entries.length && !prior) { status.textContent = message; return; }
        presentResult({lead: message, headline: message});
    }

    /* Present a finished write's result using the refresh outcome: a failed
       read stays in the status line while the summary leads the detail
       block; a refresh that turned stale while a user navigation is in
       flight defers to that landing like the C-03h pending restore. */
    function presentWriteResult(outcome, directory, names, summary, detail) {
        if (gameFilesView !== view) return;
        if (outcome === 'stale' && navInFlight) {
            afterNavRefresh = {directory, names, message:summary, detail, writeVersion};
            return;
        }
        /* A stale refresh whose newer navigation already failed keeps that
           read error in the status line — the write result moves to the
           detail block exactly like a failed refresh. */
        if (outcome === 'failed' || (outcome === 'stale' && lastReadError)) {
            presentResult({entries: detail, lead: summary, replace: true});
            return;
        }
        presentResult({entries: detail, headline: summary, own: true, replace: true});
    }

    async function pasteFiles() {
        if (!gameFilesClipboard?.items?.length || changing || !current?.path) return;
        if (!beginWorkbenchOperation('game-file-ops')) { status.textContent = tr('请等待当前任务完成。', 'Wait for the current task.'); return; }
        const items = gameFilesClipboard.items;
        const directory = current.path;
        changing = true; writeVersion += 1;
        updateActionState();
        clearPasteDetail();
        status.textContent = tr('正在粘贴…', 'Pasting…');
        try {
            const result = await runOpsJob(tr('正在复制…', 'Copying…'),
                    {action:'copy', paths: items.map(entry => entry.path), directory, root})
                .catch(error => error.result?.items ? error.result : Promise.reject(error));
            if (gameFilesView === view) {
                const names = (result?.items || [])
                    .filter(entry => entry.status === 'copied' || entry.status === 'partial')
                    .map(entry => entry.name);
                const summary = pasteSummary(result, items);
                const detail = (result?.items || []).filter(entry => entry.status === 'partial');
                const outcome = await refreshAfterWrite(directory, new Set(names), summary, detail);
                presentWriteResult(outcome, directory, new Set(names), summary, detail);
            }
        } catch (error) {
            status.textContent = gmError(error);
        } finally {
            changing = false;
            if (gameFilesView === view) updateActionState();
            endWorkbenchOperation();
        }
    }

    function pasteSummary(result, items) {
        const copied = (result?.items || []).filter(entry => entry.status === 'copied');
        const partial = (result?.items || []).filter(entry => entry.status === 'partial');
        const failed = (result?.items || []).filter(entry => entry.status === 'failed');
        const pending = (result?.items || []).filter(entry => entry.status === 'pending');
        const list = names => names.length > 4 ? names.slice(0, 4).join('、') + ' 等 ' + names.length + ' 项' : names.join('、');
        if (items.length === 1 && copied.length === 1)
            return tr('已粘贴「' + copied[0].name + '」。', 'Pasted "' + copied[0].name + '".');
        const parts = [];
        if (copied.length) parts.push(tr(`已粘贴 ${copied.length} 项`, `Pasted ${copied.length} item(s)`) + '：' + list(copied.map(entry => entry.name)));
        for (const entry of partial) parts.push(
            tr(`部分完成：${entry.name} → ${entry.target}；已写入：${list(entry.written)}（${entry.error || '中断'}）`,
               `Partially copied: ${entry.name} → ${entry.target}; written: ${list(entry.written)} (${entry.error || 'interrupted'})`));
        if (failed.length) parts.push(tr('失败：', 'Failed: ') + list(failed.map(entry => entry.name)) +
            (failed[0].error ? tr(`（${failed[0].error}）`, ` (${failed[0].error})`) : ''));
        if (pending.length) parts.push(tr(`未执行 ${pending.length} 项`, `${pending.length} item(s) not run`));
        const body = parts.length ? parts.join(tr('；', '; ')) : tr('没有粘贴任何内容。', 'Nothing was pasted.');
        return result?.cancelled ? tr('已取消：', 'Cancelled: ') + body : body;
    }

    function storageLabel(path) {
        for (const button of remote.children) {
            if (button.dataset.storage === path) return button.textContent;
        }
        return '';
    }

    async function loadDirectory(path, selectedPath = null, highlightNames = new Set()) {
        const request = ++directoryRequest;
        const isNav = path !== current?.path;
        // Only a real navigation expires pending drops and queued batches —
        // the refresh after a write reloads the same folder and must keep
        // still-valid records alive.
        if (isNav) {
            navVersion += 1;
            pendingDrops.clear();
            latestNav = request;
            navInFlight = true;
        }
        const navAppliedAtIssue = navApplied;
        directoryLoading = true;
        clearDrop();
        clearPasteDetail();
        status.textContent = tr('正在读取目录…', 'Reading folder…');
        try {
            const result = await desktopRequest('/api/game-files/directory?path=' + encodeURIComponent(path));
            if (gameFilesView !== view) return 'stale';
            if (isNav) {
                // A user navigation outranks every request issued after it.
                if (request !== latestNav) return 'stale';
                navApplied = request;
            } else if (request !== directoryRequest || navInFlight || navApplied !== navAppliedAtIssue) {
                // A refresh applies only while it is still the latest request
                // and no user navigation is pending or landed after it.
                return 'stale';
            }
            directoryLoading = false;
            const sameFolder = current?.path === result.path;
            current = result;
            const present = new Set(result.items.map(item => item.path));
            if (!sameFolder) {
                selection = new Set();
                anchor = null;
                highlights = new Set();
            } else {
                for (const kept of [...selection]) if (!present.has(kept)) selection.delete(kept);
                if (anchor && !present.has(anchor)) anchor = null;
            }
            if (highlightNames.size) highlights = highlightNames;
            if (selectedPath && present.has(selectedPath)) {
                selection = new Set([selectedPath]);
                anchor = selectedPath;
            }
            const isRemote = result.path.startsWith('mtp://');
            if (!isRemote) lastLocal = result.path;
            if (isRemote) local.removeAttribute('aria-current');
            else local.setAttribute('aria-current', 'true');
            for (const button of remote.children) {
                if (result.path.startsWith(button.dataset.storage)) button.setAttribute('aria-current', 'true');
                else button.removeAttribute('aria-current');
            }
            location.replaceChildren(...result.breadcrumbs.flatMap(({name, path}, index, crumbs) => {
                const label = (index || !isRemote ? name : '') || (isRemote ? storageLabel(path) : '') || name || tr('存储根目录', 'Storage root');
                const currentCrumb = index === crumbs.length - 1;
                const crumb = currentCrumb ? wbElement('span', '', label)
                    : gmButton(label, label, () => loadDirectory(path));
                if (currentCrumb) crumb.setAttribute('aria-current', 'page');
                if (!index) return [crumb];
                const separator = wbElement('span', 'game-files-separator', '›');
                separator.setAttribute('aria-hidden', 'true');
                return [separator, crumb];
            }));
            up.disabled = !result.parent;
            renderRows(selectedPath);
            status.textContent = result.items.length
                ? tr(result.items.length + ' 个项目', result.items.length + ' items')
                : tr('此目录没有文件。', 'This folder is empty.');
            updateActionState();
            if (isNav && (afterNavRefresh || expiredNotice === request)) {
                const pending = afterNavRefresh;
                const noticed = expiredNotice === request;
                afterNavRefresh = null;
                expiredNotice = 0;
                if (!pending) {
                    /* A notice raised during the wait with no parked write
                       still lands in the detail block. */
                    presentResult({entries: [], lead: dropExpiredMessage(), replace: true});
                } else {
                    const related = current.path === pending.directory
                        || current.items.some(item => item.directory && item.path === pending.directory);
                    const settled = related ? loadDirectory(current.path, null, pending.names)
                        : Promise.resolve('unrelated');
                    const notice = noticed ? dropExpiredMessage() : '';
                    Promise.resolve(settled).then(outcome => {
                        /* Restore the write's result when the replayed refresh
                           applied — or when the landing is unrelated so nothing
                           was replayed — provided no newer navigation landed and
                           no newer task owns the result area. A stale or failed
                           refresh keeps the newer result/read error visible. The
                           wait-time expiry notice joins the restored detail. */
                        if (gameFilesView !== view) return;
                        if (latestNav !== request || writeVersion !== pending.writeVersion) return;
                        if (outcome === 'failed') {   // keep the read error; the write result stays inspectable
                            presentResult({entries: pending.detail,
                                lead: detailLead(pending.message, notice), replace: true});
                            return;
                        }
                        if (outcome !== 'applied' && outcome !== 'unrelated') return;
                        presentResult({entries: pending.detail, lead: notice, replace: true,
                            headline: pending.message || '', own: Boolean(pending.message)});
                    });
                }
            }
            lastReadError = 0;
            if (isNav) expiredNotice = 0;   // drop any notice recorded under a superseded navigation
            return 'applied';
        } catch (error) {
            if (gameFilesView !== view || request !== directoryRequest) return 'stale';
            directoryLoading = false;
            current = null;
            selection = new Set();
            anchor = null;
            up.disabled = true;
            rows.replaceChildren();
            status.textContent = tr('读取目录失败：', 'Could not read the folder: ') + error.message;
            statusSummary = null;
            lastReadError = request;
            /* Results parked while this navigation was in flight settle
               here: the parked write result and any drop-expired notice
               raised during the wait join the detail block while the read
               error stays in the status line — and neither replays on a
               later navigation. */
            if (isNav) {
                const pending = afterNavRefresh;
                afterNavRefresh = null;
                const settled = pending && pending.writeVersion === writeVersion;
                const noticed = expiredNotice === request;
                if (settled)
                    presentResult({entries: pending.detail,
                        lead: detailLead(pending.message, noticed ? dropExpiredMessage() : ''),
                        replace: true});
                else if (noticed)
                    presentResult({lead: dropExpiredMessage()});
                expiredNotice = 0;
            }
            updateActionState();
            return 'failed';
        } finally {
            if (isNav && request === latestNav) navInFlight = false;
        }
    }

    async function addItems() {
        if (!current?.path || changing) return;
        try {
            const result = await desktopRequest('/api/desktop/pick', {kind:'items', initial:lastLocal});
            if (gameFilesView === view && result.paths?.length) confirmAdd(result.paths, current.path);
        } catch (error) {
            status.textContent = error.message;
        }
    }

    /* A queued batch's context must still be the user's current view at every
       async boundary — preview return, dialog confirm and the write itself.
       Anything stale ends the batch with feedback instead of writing to a
       directory the user already left. */
    function batchValid(batch) {
        return !batch || (batch.nav === navVersion && current?.path === batch.context);
    }

    async function confirmAdd(sources, directory, batch = null) {
        if (!directory || gameFilesView !== view) return;
        let preview;
        for (;;) {
            /* A matched batch keeps its queue slot until it issues a write or
               its own context expires — another operation holding the
               workbench only delays it. The context is re-checked after every
               release, and a preview taken while the mutex was held is thrown
               away so a fresh one sees the real conflicts. */
            while (batch && workbenchBusy()) {
                status.textContent = tr('等待当前任务完成…', 'Waiting for the current task…');
                await new Promise(resolve => setTimeout(resolve, 300));
                if (gameFilesView !== view) return;
                if (!batchValid(batch)) {
                    showDropExpired();
                    return;
                }
            }
            if (!batch && workbenchBusy()) return;
            /* Version-stamp the sample: a write that ran between this request
               and its response can have changed the landing, so the stale
               preview is discarded and the landing is sampled again. */
            const opAt = writeVersion;
            status.textContent = tr('正在检查同名项…', 'Checking for name conflicts…');
            try {
                preview = await desktopRequest('/api/game-files/import-preview', {sources, directory, root});
            } catch (error) {
                if (gameFilesView === view) status.textContent = gmError(error);
                return;
            }
            if (gameFilesView !== view) return;
            if (!batchValid(batch)) {
                showDropExpired();
                return;
            }
            if (workbenchBusy() || opAt !== writeVersion) continue;
            const conflicts = preview.items.filter(item => item.conflict);
            if (!conflicts.length && !preview.duplicates.length) { await submitAdd(sources, directory, null, batch); return; }
            status.textContent = '';
            const outcome = await openImportDialog(preview, sources, directory, conflicts, batch, opAt);
            if (outcome !== 'busy') return;
        }
    }

    function openImportDialog(preview, sources, directory, conflicts, batch = null, opAt = writeVersion) {
        const dialog = wbElement('dialog', 'desktop-confirm app-dialog game-file-import-dialog');
        const heading = wbElement('h2', '', tr('处理同名项', 'Resolve name conflicts'));
        heading.id = 'game-file-import-title';
        heading.setAttribute('autofocus', '');
        dialog.setAttribute('aria-labelledby', heading.id);
        const note = wbElement('p', '', tr('同名项默认跳过；勾选替换会以新内容占据目标名，原件移至保留位置并继续占用空间，完成后可查看保留路径。',
            'Same-named items are skipped unless approved; replacing publishes the new content under the target name while the original is kept at a retained location and still uses space — its path is shown in the result.'));
        const body = wbElement('div', 'game-file-import-body');
        const approvals = new Map();
        const memberGroup = new Map();
        preview.duplicates.forEach((group, index) => group.sources.forEach(itemIndex => memberGroup.set(itemIndex, index)));
        const chosen = new Map(preview.duplicates.map((group, index) => [index, group.sources[0]]));
        const describe = entry => (entry.folder ? tr('文件夹', 'Folder') : tr('文件', 'File')) + ' · '
            + formatFileSize(entry.size) + ' · ' + formatFileTime(entry.modified);
        for (const item of conflicts) {
            const row = wbElement('label', 'game-file-conflict');
            const box = wbElement('input', '');
            box.type = 'checkbox';
            const text = wbElement('span', 'game-file-conflict-text');
            text.append(wbElement('strong', '', item.name),
                wbElement('span', '', tr('现有：' + describe(item.conflict), 'Existing: ' + describe(item.conflict))),
                wbElement('span', '', tr('导入：' + describe(item), 'Incoming: ' + describe(item))));
            if (item.conflict.folder || item.folder) {
                text.append(wbElement('span', 'game-file-conflict-warning',
                    tr('整项替换，原件移至保留位置。', 'Replaces the whole item; the original is moved to a retained location.')));
            }
            approvals.set(item.source, box);
            row.append(box, text);
            body.append(row);
        }
        preview.duplicates.forEach((group, index) => {
            const set = wbElement('fieldset', 'game-file-dup');
            set.append(wbElement('legend', '', tr(`「${group.name}」有 ${group.sources.length} 个同名来源，选择一个复制`,
                `"${group.name}" has ${group.sources.length} sources; choose one to copy`)));
            group.sources.forEach((itemIndex, option) => {
                const item = preview.items[itemIndex];
                const label = wbElement('label', 'game-file-dup-option');
                const radio = wbElement('input', '');
                radio.type = 'radio';
                radio.name = 'game-file-dup-' + index;
                radio.checked = !option;
                radio.onchange = () => { if (radio.checked) { chosen.set(index, itemIndex); updateConflictAccess(); } };
                label.append(radio, wbElement('span', '', `${item.source}（${describe(item)}）`));
                set.append(label);
            });
            body.append(set);
        });
        function updateConflictAccess() {
            preview.items.forEach((item, index) => {
                const box = approvals.get(item.source);
                const group = memberGroup.get(index);
                if (box) box.disabled = group !== undefined && chosen.get(group) !== index;
            });
        }
        updateConflictAccess();
        const message = wbElement('p', '', '');
        message.setAttribute('role', 'status');
        const actions = wbElement('div', 'desktop-confirm-actions');
        actions.append(gmButton('取消', 'Cancel', () => dialog.close()));
        if (conflicts.length > 1) {
            actions.append(gmButton('全部替换', 'Replace all', () => {
                approvals.forEach(box => { if (!box.disabled) box.checked = true; });
            }));
        }
        actions.append(gmButton('继续复制', 'Continue', () => {
            const decisions = {};
            preview.items.forEach((item, index) => {
                const group = memberGroup.get(index);
                if (group !== undefined && chosen.get(group) !== index) {
                    decisions[item.source] = {action:'skip'};
                } else if (item.conflict) {
                    decisions[item.source] = approvals.get(item.source).checked
                        ? {action:'replace', stamp:item.conflict.stamp} : {action:'skip'};
                } else {
                    decisions[item.source] = {action:'copy'};
                }
            });
            if (!batchValid(batch)) {
                showDropExpired();
                finish();
                dialog.close();
                return;
            }
            if (workbenchBusy() || opAt !== writeVersion) {
                /* Another write ran while the dialog was open — the sampled
                   conflicts may be stale; close and re-check after release. */
                finish('busy');
                dialog.close();
                return;
            }
            finish(submitAdd(sources, directory, decisions, batch));
            dialog.close();
        }, false));
        dialog.append(heading, note, body, message, actions);
        document.body.append(dialog);
        let finish = () => {};
        const settled = new Promise(resolve => { finish = resolve; });
        dialog.addEventListener('close', () => { dialog.remove(); finish(); }, {once:true});
        dialog.showModal();
        return settled;
    }

    async function submitAdd(sources, directory, decisions = null, batch = null) {
        if (!directory || changing || !sources.length) return;
        if (!batchValid(batch)) {
            showDropExpired();
            return;
        }
        if (!beginWorkbenchOperation('game-file-ops')) { status.textContent = tr('请等待当前任务完成。', 'Wait for the current task.'); return; }
        changing = true; writeVersion += 1;
        updateActionState();
        clearPasteDetail();
        status.textContent = tr('正在复制…', 'Copying…');
        const payload = {action:'add', sources, directory, root};
        if (decisions) payload.decisions = decisions;
        try {
            const result = await runOpsJob(tr('正在复制…', 'Copying…'), payload);
            if (gameFilesView === view) {
                // Refresh whatever the user is looking at — a write into a
                // subfolder must not navigate the view to the target.
                const summary = addSummary(result);
                const detail = [
                    ...(result.failed || []).filter(entry => entry.written?.length || entry.conflict || entry.interrupted || entry.target),
                    ...(result.leftovers || []).map(entry => ({name: entry.name, target: entry.path, retained: entry.reason})),
                ];
                const outcome = await refreshAfterWrite(directory, new Set([...result.copied, ...result.replaced]), summary, detail);
                presentWriteResult(outcome, directory, new Set([...result.copied, ...result.replaced]), summary, detail);
            }
        } catch (error) {
            status.textContent = gmError(error);
        } finally {
            changing = false;
            if (gameFilesView === view) updateActionState();
            endWorkbenchOperation();
        }
    }

    function addSummary(result) {
        const parts = [];
        const list = names => names.length > 4 ? names.slice(0, 4).join('、') + ' 等 ' + names.length + ' 项' : names.join('、');
        if (result.copied.length) parts.push(tr('已复制：', 'Copied: ') + list(result.copied));
        if (result.replaced?.length) parts.push(tr('已替换：', 'Replaced: ') + list(result.replaced));
        if (result.skipped.length) parts.push(tr('已跳过（同名已存在）：', 'Skipped (same name exists): ') + list(result.skipped));
        for (const entry of result.kept || []) {
            parts.push(entry.reason === 'changed'
                ? tr(`目标已变化，已保留：${entry.name}`, `Target changed; kept: ${entry.name}`)
                : entry.reason === 'unverifiable'
                    ? tr(`无法核对目标内容，已保留：${entry.name}`, `Could not verify the target; kept: ${entry.name}`)
                    : tr(`预览后新增同名目标，已保留：${entry.name}`, `Target appeared after preview; kept: ${entry.name}`));
        }
        if (result.failed.length) parts.push(tr('失败：', 'Failed: ') + list(result.failed.map(entry => entry.name)));
        const recoverable = result.failed.flatMap(entry => entry.recoverable || []);
        if (recoverable.length) parts.push(tr('可找回对象：', 'Recoverable at: ') + list(recoverable));
        if (result.remaining.length) parts.push(tr('未完成：', 'Not finished: ') + list(result.remaining));
        if (result.leftovers?.length) parts.push(tr('残留待核对：', 'Leftover to verify: ')
            + list(result.leftovers.map(entry => entry.reason ? entry.path + '（' + entry.reason + '）' : entry.path)));
        return parts.length ? parts.join(tr('；', '; ')) : tr('没有可复制的内容。', 'Nothing to copy.');
    }
}

async function refreshDeletedPlaylists(paths) {
    if (!paths.length) return;
    const active = paths.includes(gameLibraryInput()[0]);
    if (active) retryGameLibrary();
    await refreshLibraryPlaylists(paths);
}

/* Folders always precede files; both groups follow the chosen direction. */
function gameFilesSorted(items, direction) {
    const dir = direction === 'desc' ? -1 : 1;
    const key = name => name.toLowerCase();
    return [...items].sort((a, b) => ((b.directory ? 1 : 0) - (a.directory ? 1 : 0))
        || dir * (key(a.name) < key(b.name) ? -1 : key(a.name) > key(b.name) ? 1
            : a.name < b.name ? -1 : a.name > b.name ? 1 : 0));
}

/* Playlist-sync registrations from a batch delete result: a fully removed
   folder registers its own path; a partial one registers only deleted files. */
function fileChangeRegistrations(result) {
    const entries = [];
    for (const item of result?.items || []) {
        if (item.status === 'deleted') entries.push({action:'delete', path:item.path, folder:Boolean(item.folder)});
        else if (item.status === 'partial') {
            for (const path of item.deleted || []) entries.push({action:'delete', path, folder:false});
        }
    }
    return entries;
}

function formatFileSize(value) {
    if (value == null || Number.isNaN(value)) return workbenchText('未知', 'Unknown');
    const units = ['B', 'KB', 'MB', 'GB', 'TB'];
    let size = value, unit = 0;
    while (size >= 1024 && unit < units.length - 1) { size /= 1024; unit += 1; }
    return (unit ? size.toFixed(size >= 10 ? 0 : 1) : size) + ' ' + units[unit];
}

function formatFileTime(value) {
    if (value == null) return workbenchText('未知', 'Unknown');
    return new Date(value * 1000).toLocaleString(currentUILanguage === 'en' ? 'en-US' : 'zh-CN');
}

/* The directory a recorded drop zone resolves to, or null when the drop is no
   longer valid (stale event, page navigated, directory reloading, or the
   folder row it hovered is gone). Exported for regression tests. */
function gameFilesDropDirectory(zone, current, navVersion, busy, stamp) {
    if (!zone?.dropped || zone.stamp !== stamp || stamp == null || zone.nav !== navVersion
            || busy || !current?.path || zone.context !== current.path) return null;
    if (!zone.directory || (zone.directory !== current.path
            && !current.items.some(item => item.directory && item.path === zone.directory))) return null;
    return zone.directory;
}

/* Called by the desktop shell with native paths from a Finder drop. */
function gameFilesExternalDrop(paths, stamp) {
    gameFilesView?.externalDrop?.(paths, stamp);
}

/* Called by the native capture with this drop's nonce-bound source paths. */
function gameFilesDropSource(payload) {
    gameFilesView?.dropSource?.(payload);
}

if (typeof module !== 'undefined') module.exports = {gameFilesSorted, fileChangeRegistrations, formatFileSize, formatFileTime, gameFilesDropDirectory};
