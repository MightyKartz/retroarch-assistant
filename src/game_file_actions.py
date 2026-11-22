"""Plain file-manager operations: rename, delete, copy."""
from pathlib import Path
import ctypes
import errno
import hashlib
import itertools
import json
import os
import shutil
import sys
import tempfile
import uuid

import chd_io
import mtp_runtime as mtp
from safe_io import file_lock
from task_control import TaskCancelled


if sys.platform == 'darwin':
    _libsystem = ctypes.CDLL('/usr/lib/libSystem.B.dylib', use_errno=True)
    _renamex_np = _libsystem.renamex_np
    _renamex_np.argtypes = [ctypes.c_char_p, ctypes.c_char_p, ctypes.c_uint]
    # RENAME_EXCL: the kernel refuses to publish over an existing target, on
    # every filesystem the volume supports (including ExFAT, where hard links
    # are unavailable).
    _RENAME_EXCL = 0x00000004


class PublishConflict(FileExistsError):
    """A no-clobber refusal on a member inside an already-claimed directory.

    `member` is the conflicting destination path; `moved` counts and
    `written` lists the members already published into the claimed
    directory, so the report can name the real completed range."""

    def __init__(self, member, moved=0, written=None):
        super().__init__(errno.EEXIST, '目标内出现同名成员', member)
        self.member = member
        self.moved = moved
        self.written = list(written or [])

    def __str__(self):
        return '目标内出现同名成员「' + str(self.member) + '」已保留；此前已发布 ' + str(self.moved) + ' 项'


class PublishAbort(Exception):
    """A member write failed mid-publish — carries the real completed range.

    `member` is the interrupted destination path; `moved`/`written` mirror
    PublishConflict so the report can name every path already published."""

    def __init__(self, member, moved=0, written=None):
        super().__init__('成员「' + str(member) + '」发布中断；此前已发布 ' + str(moved) + ' 项')
        self.member = member
        self.moved = moved
        self.written = list(written or [])


def _rename_exclusive(staged, target):
    """Move `staged` onto `target` without ever replacing existing content.

    Raises FileExistsError when the target name itself is already taken. The
    claim is always atomic — macOS uses renamex_np with RENAME_EXCL where the
    volume allows it, POSIX uses a hard link, and on linkless filesystems
    (ExFAT/FAT) the name is claimed with O_EXCL and the bytes are written
    through the held descriptor, so a swapped-in file is never touched.
    Directories claim the top name with mkdir, then publish every member with
    the same exclusive semantics; a member-level refusal raises
    PublishConflict so the caller can report the real completed range."""
    if sys.platform == 'darwin':
        ctypes.set_errno(0)
        if _renamex_np(os.fsencode(staged), os.fsencode(target), _RENAME_EXCL) == 0:
            return
        error = ctypes.get_errno()
        if error == errno.EEXIST:
            raise FileExistsError(errno.EEXIST, '目标已存在', target)
        if error not in (errno.ENOTSUP, errno.EINVAL):
            raise OSError(error, os.strerror(error))
        # This volume lacks RENAME_EXCL (e.g. ExFAT) — fall through to the
        # portable claim below, which is atomic on any POSIX filesystem.
    staged_path = Path(staged)
    if staged_path.is_dir() and not staged_path.is_symlink():
        os.mkdir(target)  # mkdir itself refuses an existing name
        target_path = Path(target)
        moved = 0
        written = []
        # Sorted so the written-prefix a conflict reports is deterministic.
        for child in sorted(staged_path.iterdir()):
            if child.name.startswith('._'):
                # A sidecar that appeared inside our own staging tree is not
                # a member worth publishing or conflicting over.
                continue
            try:
                nested = _rename_exclusive(str(child), str(target_path / child.name)) or []
            except FileExistsError as error:
                if isinstance(error, PublishConflict):
                    error.moved += moved
                    error.written = written + [child.name + '/' + entry for entry in error.written]
                    raise
                member = (getattr(error, 'filename2', None) or error.filename
                          or str(target_path / child.name))
                raise PublishConflict(member, moved, written) from error
            except PublishAbort as error:
                # A nested member aborted: prefix its written range with the
                # members published at this level so the report stays real.
                error.moved += moved
                error.written = written + [child.name + '/' + entry for entry in error.written]
                raise
            except FileNotFoundError:
                if child.exists():
                    raise
                # Transient entries (e.g. ExFAT AppleDouble sidecars) can
                # vanish between listing and rename; nothing to move.
            except Exception as error:
                raise PublishAbort(str(target_path / child.name), moved, written) from error
            else:
                moved += 1
                # A completed subdirectory reports the actual file paths it
                # published inside, so a later abort names every written file.
                written.extend(child.name + '/' + entry for entry in nested)
                if not nested:
                    written.append(child.name)
        try:
            staged_path.rmdir()
        except OSError:
            # Skipped sidecars still occupy the staging dir.
            shutil.rmtree(staged_path, ignore_errors=True)
        return written
    try:
        os.link(staged, target)
    except FileExistsError:
        raise
    except OSError:
        flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, 'O_BINARY', 0)
        fd = os.open(target, flags, 0o644)
        try:
            if hasattr(os, 'fchmod'):
                os.fchmod(fd, staged_path.stat().st_mode & 0o777)
            with open(staged, 'rb') as source:
                # Writing through the claimed descriptor can never reach a
                # file that was swapped in after the claim; the context
                # manager flushes the buffer, and a flush failure surfaces
                # here instead of being reported as a successful copy.
                with os.fdopen(fd, 'wb', closefd=False) as writer:
                    shutil.copyfileobj(source, writer)
            os.fsync(fd)
            claimed = os.fstat(fd)
            actual = os.stat(target)
            if (claimed.st_dev, claimed.st_ino) != (actual.st_dev, actual.st_ino):
                raise FileExistsError(errno.EEXIST, '目标已被替换', target)
        except BaseException as error:
            ours = False
            try:
                claimed = os.fstat(fd)
                actual = os.stat(target)
                ours = (claimed.st_dev, claimed.st_ino) == (actual.st_dev, actual.st_ino)
            except OSError:
                pass
            os.close(fd)
            if ours:
                # POSIX cannot unlink by held identity — a claimant swapped in
                # after this check would be deleted too. Leave the claimed
                # file and name the real path so the report can mark it for
                # a human check instead of pretending a clean rollback.
                error.leftover = str(target)
            raise
        os.close(fd)
    staged_path.unlink()


def validate_op(payload):
    """Boundary and argument checks for plain file-manager operations."""
    action = payload.get('action')
    path, root = payload.get('path'), payload.get('root')
    if action not in {'rename', 'delete', 'copy'} or not isinstance(path, str) or not isinstance(root, str):
        raise ValueError('文件操作参数无效')
    remote = mtp.is_mtp(path)
    if remote:
        serial, _, parts = mtp.parse(path)
        if not parts or not mtp.is_mtp(root) or mtp.parse(root)[0] != serial:
            raise ValueError('请先打开此掌机的 RetroArch 游戏库')
    elif not Path(path).is_absolute():
        raise ValueError('请选择本地文件')
    if action == 'rename':
        name = payload.get('name')
        if (not isinstance(name, str) or not name or name in {'.', '..'}
                or '/' in name or '\\' in name or '\x00' in name):
            raise ValueError('请输入有效文件名')
        return path, name, remote
    if action == 'copy':
        directory = payload.get('directory')
        if not isinstance(directory, str) or mtp.is_mtp(directory) != remote:
            raise ValueError('目标目录无效')
        if remote and mtp.parse(directory)[0] != mtp.parse(path)[0]:
            raise ValueError('目标目录必须属于同一台设备')
        if remote:
            if mtp.parse(directory)[2][:len(mtp.parse(path)[2])] == mtp.parse(path)[2]:
                raise ValueError('不能复制到自身或它的子目录')
        elif not Path(directory).is_dir():
            raise ValueError('目标目录不存在')
        elif Path(path).is_dir() and Path(directory).resolve().is_relative_to(Path(path).resolve()):
            raise ValueError('不能复制到自身或它的子目录')
        return path, directory, remote
    return path, None, remote


def validate_ops(payload):
    """Batch boundary checks for copy/delete over an explicit path list.

    All paths must live in the same storage (local or one MTP device); the list
    is normalized and deduplicated before any write happens."""
    action, paths, root = payload.get('action'), payload.get('paths'), payload.get('root')
    if (action not in {'delete', 'copy'} or not isinstance(paths, list) or not paths
            or len(paths) > 500 or not isinstance(root, str)):
        raise ValueError('文件操作参数无效')
    normalized, seen, remote = [], set(), None
    for path in paths:
        if not isinstance(path, str):
            raise ValueError('文件操作参数无效')
        is_remote = mtp.is_mtp(path)
        if is_remote:
            serial, _, parts = mtp.parse(path)
            if not parts or not mtp.is_mtp(root) or mtp.parse(root)[0] != serial:
                raise ValueError('请先打开此掌机的 RetroArch 游戏库')
        elif not Path(path).is_absolute():
            raise ValueError('请选择本地文件')
        if remote is None:
            remote = is_remote
        elif is_remote != remote:
            raise ValueError('文件操作参数无效')
        key = path if is_remote else str(Path(path))
        if key not in seen:
            seen.add(key)
            normalized.append(path)
    if action == 'delete':
        return normalized, None, remote
    directory = payload.get('directory')
    if not isinstance(directory, str) or mtp.is_mtp(directory) != remote:
        raise ValueError('目标目录无效')
    if remote:
        if mtp.parse(directory)[0] != mtp.parse(normalized[0])[0]:
            raise ValueError('目标目录必须属于同一台设备')
        for path in normalized:
            if mtp.parse(directory)[2][:len(mtp.parse(path)[2])] == mtp.parse(path)[2]:
                raise ValueError('不能复制到自身或它的子目录')
    else:
        if not Path(directory).is_dir():
            raise ValueError('目标目录不存在')
        for path in normalized:
            if Path(path).is_dir() and Path(directory).resolve().is_relative_to(Path(path).resolve()):
                raise ValueError('不能复制到自身或它的子目录')
    return normalized, directory, remote


def validate_add(payload):
    """Local files/folders copied into the browsed directory (local or MTP)."""
    sources, directory, root = payload.get('sources'), payload.get('directory'), payload.get('root')
    if (not isinstance(sources, list) or not sources or len(sources) > 500
            or any(not isinstance(source, str) or mtp.is_mtp(source) for source in sources)
            or not isinstance(directory, str) or not isinstance(root, str)):
        raise ValueError('添加参数无效')
    remote = mtp.is_mtp(directory)
    if remote:
        if not mtp.is_mtp(root) or mtp.parse(root)[0] != mtp.parse(directory)[0]:
            raise ValueError('请先打开此掌机的 RetroArch 游戏库')
    elif not Path(directory).is_dir():
        raise ValueError('目标目录不存在')
    else:
        destination = Path(directory).resolve()
        if any(Path(source).resolve().is_dir() and destination.is_relative_to(Path(source).resolve())
               for source in sources):
            raise ValueError('不能把文件夹复制到自身内部')
    for source in sources:
        if not Path(source).is_absolute() or not Path(source).exists():
            raise ValueError('请选择电脑上存在的文件或文件夹')
    return [str(Path(source)) for source in sources], directory, remote, payload.get('overwrite') is True


def validate_decisions(payload, sources):
    """Approved per-source actions bound to a server-side conflict preview.

    `sources` is the normalized list returned by validate_add; each decision is
    {'action': 'copy'|'skip'|'replace', 'stamp': optional target fingerprint}.
    Unknown sources are rejected so a tampered batch cannot widen its scope."""
    decisions = payload.get('decisions')
    if decisions is None:
        return None
    if not isinstance(decisions, dict):
        raise ValueError('添加参数无效')
    normalized = {}
    for source, decision in decisions.items():
        key = str(Path(source)) if isinstance(source, str) else source
        if (key not in sources or not isinstance(decision, dict)
                or decision.get('action') not in {'copy', 'skip', 'replace'}):
            raise ValueError('添加参数无效')
        stamp = decision.get('stamp')
        if stamp is not None and not isinstance(stamp, str):
            raise ValueError('添加参数无效')
        normalized[key] = {'action': decision['action'], 'stamp': stamp}
    return normalized


def _file_digest(path):
    """SHA-256 of a file's content — the evidence that still separates two
    files whose size and filesystem mtime both survived a same-length edit
    (ExFAT reports coarse timestamps and no content checksum)."""
    digest = hashlib.sha256()
    with open(path, 'rb') as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b''):
            digest.update(block)
    return digest.hexdigest()


def target_stamp(target, remote):
    """Fingerprint of an existing destination, used to re-verify replace approvals.

    Directories fingerprint their full member tree (relative path, type, size,
    mtime, and for local members a content digest) because a folder's own
    mtime and entry count cannot see in-place edits of member contents.
    Returns None when nothing exists; raises when the target cannot be fully
    inspected."""
    if remote:
        entry = mtp.stat_file(target)
        if entry is None:
            return None
        if not entry['folder']:
            return json.dumps({'id': entry['id'], 'folder': False,
                               'size': entry.get('size'), 'modified': entry.get('modified'),
                               'verifiable': (entry.get('id') is not None
                                              and entry.get('modified') is not None)},
                              sort_keys=True)
        base = len(mtp.parse(target)[2])
        files, folders = _walk_remote(target)
        members = sorted([('/'.join(mtp.parse(folder['path'])[2][base:]), True, folder['id'], None, None)
                          for folder in folders[1:]]
                         + [(file['relative'], False, file['id'], file.get('size'),
                             file.get('modified')) for file in files])
        # Per node type: a folder only anchors its object id and structure,
        # while a file additionally needs a timestamp to spot in-place edits
        # (MTP folders report no reliable mtime of their own).
        verifiable = all(member[2] is not None if member[1]
                         else member[2] is not None and member[4] is not None
                         for member in members)
        return json.dumps({'folder': True, 'tree': members, 'verifiable': verifiable},
                          sort_keys=True, default=str)
    path = Path(target)
    try:
        stat = path.stat()
    except OSError:
        return None
    if not (path.is_dir() and not path.is_symlink()):
        digest = _file_digest(path)
        after = path.stat()
        # The digest only proves the content read between two identical stats.
        consistent = (after.st_size, after.st_mtime_ns) == (stat.st_size, stat.st_mtime_ns)
        return json.dumps({'folder': False, 'size': stat.st_size, 'mtime': stat.st_mtime_ns,
                           'digest': digest, 'verifiable': consistent}, sort_keys=True)
    members = []
    # Sidecar entries churn independently of real content; a stamp that
    # watches them reports phantom changes on ExFAT volumes.
    snapshot = sorted(entry for entry in path.rglob('*')
                      if not any(part.startswith('._') for part in entry.relative_to(path).parts))
    consistent = True
    for entry in snapshot:
        stat0 = entry.lstat()
        is_dir = entry.is_dir() and not entry.is_symlink()
        digest = None if is_dir or not entry.is_file() else _file_digest(entry)
        # A member changed while its own bytes were being read is not evidence.
        if not is_dir:
            stat1 = entry.lstat()
            if (stat1.st_size, stat1.st_mtime_ns) != (stat0.st_size, stat0.st_mtime_ns):
                consistent = False
        members.append([str(entry.relative_to(path)), is_dir,
                        stat0.st_size, stat0.st_mtime_ns, digest])
    if consistent:
        # A member edited while a LATER member was being digested produces a
        # stamp that mixes two moments. Re-verify content, not only metadata:
        # an equal-size, same-mtime rewrite passes a size/mtime comparison,
        # so every file is digested a second time and must match byte-for-byte.
        followup = sorted(entry for entry in path.rglob('*')
                          if not any(part.startswith('._') for part in entry.relative_to(path).parts))
        if len(followup) != len(snapshot):
            consistent = False
        else:
            for index, entry in enumerate(followup):
                record = members[index]
                stat2 = entry.lstat()
                if (str(entry.relative_to(path)) != record[0]
                        or (stat2.st_size, stat2.st_mtime_ns) != (record[2], record[3])
                        or (record[4] is not None and _file_digest(entry) != record[4])):
                    consistent = False
                    break
    return json.dumps({'folder': True, 'tree': members, 'verifiable': consistent}, sort_keys=True)


def _stamp_verifiable(stamp):
    """False when the fingerprint lacks the data to notice silent edits."""
    try:
        return json.loads(stamp).get('verifiable', True) is not False
    except (TypeError, ValueError, AttributeError):
        return False


def preview_import(sources, directory, remote, light=False):
    """Server-side conflict preview for local sources copied into `directory`.

    Reports both sides' name/type/size/mtime (None where unavailable) plus a
    target `stamp` the caller returns when approving a replace, and groups of
    sources that map to the same destination name. `light` skips fingerprinting
    entirely — the simple overwrite prompt never sends stamps back."""
    def stamp_of(target):
        if light:
            return None
        try:
            return target_stamp(target, remote)
        except Exception:
            return None
    items = []
    for source in sources:
        path = Path(source)
        folder = path.is_dir()
        stat = path.stat()
        target = mtp.join(directory, path.name) if remote else str(Path(directory) / path.name)
        conflict = None
        if remote:
            entry = mtp.stat_file(target)
            if entry is not None:
                conflict = {'folder': entry['folder'],
                            'size': None if entry['folder'] else entry.get('size'),
                            'modified': entry.get('modified'), 'stamp': stamp_of(target)}
        elif Path(target).exists() or Path(target).is_symlink():
            existing = Path(target)
            is_dir = existing.is_dir() and not existing.is_symlink()
            try:
                target_stat = existing.stat()
            except OSError:
                target_stat = None
            conflict = {'folder': is_dir, 'size': None if is_dir or target_stat is None else target_stat.st_size,
                        'modified': None if target_stat is None else target_stat.st_mtime,
                        'stamp': stamp_of(target)}
        items.append({'source': str(path), 'name': path.name, 'folder': folder,
                      'size': None if folder else stat.st_size, 'modified': stat.st_mtime,
                      'target': target, 'conflict': conflict, 'duplicate': False})
    groups = {}
    for index, item in enumerate(items):
        groups.setdefault(item['name'].casefold(), []).append(index)
    duplicates = []
    for indexes in groups.values():
        if len(indexes) < 2:
            continue
        for index in indexes:
            items[index]['duplicate'] = True
        duplicates.append({'name': items[indexes[0]]['name'], 'sources': indexes})
    return {'directory': directory, 'items': items, 'duplicates': duplicates}


def _walk_remote(path):
    """All descendant files and folders (with object ids) under an MTP path."""
    entry = mtp.stat_file(path)
    if entry is None:
        raise ValueError('文件已移动或不存在')
    if not entry['folder']:
        return [{'path': path, 'relative': mtp.parse(path)[2][-1], 'id': entry['id'],
                 'size': entry.get('size'), 'modified': entry.get('modified')}], []
    files, folders, queue = [], [{'path': path, 'id': entry['id']}], [(path, '')]
    while queue:
        current, rel_prefix = queue.pop(0)
        for child in mtp.listing(current):
            child_path = mtp.join(current, child['name'])
            relative = rel_prefix + '/' + child['name'] if rel_prefix else child['name']
            if child['folder']:
                folders.append({'path': child_path, 'id': child['id']})
                queue.append((child_path, relative))
            else:
                files.append({'path': child_path, 'relative': relative, 'id': child['id'],
                              'size': child.get('size'), 'modified': child.get('modified')})
    return files, folders


def apply_rename(path, name, remote):
    if remote:
        return {'new_path': mtp.rename_object(path, name)}
    source = Path(path)
    if not source.exists():
        raise ValueError('文件已移动或不存在')
    target = source.with_name(name)
    if target.exists() or target.is_symlink():
        raise ValueError('已有同名文件')
    with file_lock(path):
        source.rename(target)
    return {'new_path': str(target)}


def _ensure_remote_dir(path):
    with mtp.session(path) as (handle, storage, parts):
        mtp.resolve(handle, storage, parts, create=True)


def apply_delete(path, remote, progress=lambda *args: None, cancelled=lambda: None):
    """Permanently delete a file or folder.

    Returns {'path', 'deleted', 'cancelled'}: the files actually removed stay
    reported even when cancellation stops the sweep partway."""
    deleted = []
    try:
        if not remote:
            source = Path(path)
            if not source.exists() and not source.is_symlink():
                raise ValueError('文件已移动或不存在')
            cancelled()
            with file_lock(path):
                if source.is_dir() and not source.is_symlink():
                    entries = sorted(source.rglob('*'), key=lambda p: len(p.parts), reverse=True)
                    total = len(entries)
                    for index, entry in enumerate(entries):
                        cancelled()
                        progress(index + 1, total, '删除：' + str(entry))
                        if entry.is_dir() and not entry.is_symlink():
                            entry.rmdir()
                        else:
                            entry.unlink()
                            deleted.append(str(entry))
                    source.rmdir()
                else:
                    source.unlink()
                    deleted.append(path)
            return {'path': path, 'deleted': deleted, 'cancelled': False}
        files, folders = _walk_remote(path)
        for index, entry in enumerate(files):
            cancelled()
            progress(index, len(files), '删除：' + entry['path'])
            mtp.delete_object(entry['path'])
            deleted.append(entry['path'])
        for folder in sorted(folders, key=lambda f: len(mtp.parse(f['path'])[2]), reverse=True):
            cancelled()
            mtp.delete_object(folder['path'])
        return {'path': path, 'deleted': deleted, 'cancelled': False}
    except TaskCancelled:
        return {'path': path, 'deleted': deleted, 'cancelled': True}
    except (OSError, ValueError, RuntimeError) as error:
        if not deleted:
            raise
        return {'path': path, 'deleted': deleted, 'cancelled': False, 'error': str(error)}


def _is_folder(path, remote):
    if remote:
        entry = mtp.stat_file(path)
        return bool(entry and entry['folder'])
    return Path(path).is_dir() and not Path(path).is_symlink()


def apply_delete_batch(paths, remote, progress=lambda *args: None, cancelled=lambda: None):
    """Sequentially delete every item, reporting per-item status.

    Statuses: 'deleted' (complete), 'partial' (some content removed before an
    error or cancellation), 'failed' (nothing removed), 'pending' (not run).
    The batch stops at the first incomplete item and marks the rest pending."""
    items = []
    result = {'items': items, 'cancelled': False}
    for index, path in enumerate(paths):
        entry = {'path': path, 'name': _source_leaf(path, remote), 'folder': None, 'deleted': []}
        try:
            entry['folder'] = _is_folder(path, remote)
            outcome = apply_delete(path, remote,
                                   lambda done, total, msg: progress(index, len(paths), msg),
                                   cancelled)
        except Exception as error:
            entry.update(status='failed', error=str(error))
            items.append(entry)
            break
        complete = not outcome.get('cancelled') and not outcome.get('error')
        entry['deleted'] = outcome['deleted']
        entry['status'] = 'deleted' if complete else ('partial' if outcome['deleted'] else 'failed')
        if not complete:
            entry['error'] = outcome.get('error') or '已取消'
        items.append(entry)
        if not complete:
            result['cancelled'] = bool(outcome.get('cancelled'))
            break
    # Unrun entries are built from what is already known; a dead device must
    # not lose the confirmed results collected above.
    for path in paths[len(items):]:
        items.append({'path': path, 'name': _source_leaf(path, remote), 'folder': None,
                      'status': 'pending', 'deleted': []})
    return result


def _unique_name(directory, name, remote):
    def exists(candidate):
        target = mtp.join(directory, candidate) if remote else str(Path(directory) / candidate)
        return mtp.stat_file(target) is not None if remote else Path(target).exists()
    if not exists(name):
        return name
    stem, dot, ext = name.rpartition('.')
    base, suffix = (stem, dot + ext) if dot and stem else (name, '')
    for index in itertools.count(1):
        candidate = base + ' 副本' + (f' {index}' if index > 1 else '') + suffix
        if not exists(candidate):
            return candidate


def _source_leaf(path, remote):
    """Display name of the copy source on its own platform."""
    return mtp.parse(path)[2][-1] if remote else Path(path).name


class CopyPartial(RuntimeError):
    """A remote copy stopped mid-folder; `report` names the real destination
    and the member paths actually written, so the result can point at what
    remains on the device instead of hiding it under a plain failure."""


def apply_copy(path, directory, remote, progress=lambda *args: None, cancelled=lambda: None):
    """Copy a file or folder into a directory, auto-renaming on conflict.

    Returns {'path', 'name'} where name is the final display name of the new
    copy (which may have been auto-renamed on conflict)."""
    name = _unique_name(directory, _source_leaf(path, remote), remote)
    if not remote:
        source = Path(path)
        target = Path(directory) / name
        if not source.exists():
            raise ValueError('文件已移动或不存在')
        cancelled()
        try:
            if source.is_dir():
                folders = sorted(p for p in source.rglob('*') if p.is_dir() and not p.is_symlink())
                files = sorted(p for p in source.rglob('*') if not p.is_dir() or p.is_symlink())
                target.mkdir()
                for entry in folders:
                    (target / entry.relative_to(source)).mkdir(parents=True, exist_ok=True)
                for index, entry in enumerate(files):
                    cancelled()
                    progress(index + 1, len(files), '复制：' + entry.name)
                    shutil.copy2(entry, target / entry.relative_to(source))
            else:
                with file_lock(path):
                    shutil.copy2(source, target)
        except Exception:
            _remove_destination(str(target), False)
            raise
        return {'path': str(target), 'name': name}
    destination = mtp.join(directory, name)
    files, folders = _walk_remote(path)
    if folders:
        _ensure_remote_dir(destination)
        for folder in folders[1:]:
            _ensure_remote_dir(destination + folder['path'][len(path):])
    written = []
    try:
        for index, entry in enumerate(files):
            cancelled()
            progress(index, len(files), '复制：' + entry['path'])
            target = mtp.join(destination, entry['relative']) if folders else destination
            _remote_copy_file(entry['path'], target, cancelled)
            written.append(target)
    except Exception as error:
        partial = CopyPartial(str(error))
        partial.report = {'path': destination, 'name': name, 'written': written}
        partial.cancelled = isinstance(error, TaskCancelled)
        raise partial from error
    return {'path': destination, 'name': name}


def apply_copy_single(path, directory, remote, progress=lambda *args: None, cancelled=lambda: None):
    """Single-item copy preserving the legacy API contract: cancellation
    surfaces as TaskCancelled, real failures as plain errors. The shared
    remote implementation's CopyPartial is unwrapped here; batch callers read
    its `report` for per-file detail instead."""
    try:
        return apply_copy(path, directory, remote, progress, cancelled)
    except CopyPartial as error:
        if getattr(error, 'cancelled', False):
            raise TaskCancelled(str(error)) from error
        raise RuntimeError(str(error)) from error


def apply_copy_batch(paths, directory, remote, progress=lambda *args: None, cancelled=lambda: None):
    """Sequentially copy every item into `directory`, reporting per-item status.

    Statuses: 'copied' (with the final output `name`), 'failed', 'pending'.
    The batch stops at the first failed item and marks the rest pending."""
    items = []
    result = {'items': items, 'cancelled': False}
    for index, path in enumerate(paths):
        try:
            outcome = apply_copy(path, directory, remote,
                                 lambda done, total, msg: progress(index, len(paths), msg),
                                 cancelled)
            items.append({'path': path, 'status': 'copied', 'name': outcome['name']})
        except CopyPartial as error:
            items.append({'path': path, 'status': 'partial' if error.report['written'] else 'failed',
                          'name': error.report['name'], 'target': error.report['path'],
                          'written': error.report['written'], 'error': str(error)})
            result['cancelled'] = bool(error.cancelled)
            break
        except Exception as error:
            items.append({'path': path, 'status': 'failed', 'error': str(error)})
            result['cancelled'] = isinstance(error, TaskCancelled)
            break
    for path in paths[len(items):]:
        items.append({'path': path, 'status': 'pending'})
    return result


def _remote_copy_file(source, destination, cancelled):
    """Download to a temp file, then upload verified; cleans up a stray stage on failure."""
    with tempfile.TemporaryDirectory(prefix='plcn-copy-') as temporary:
        local = Path(temporary) / 'payload'
        chd_io.copy_to(source, local, cancelled)
        _upload_local(local, destination, cancelled)


def _upload_local(source, destination, cancelled):
    expected = chd_io.digest(str(source), cancelled)
    stage = destination + '.plcn-' + uuid.uuid4().hex
    try:
        mtp.upload_file(destination, str(source), expected, stage, cancelled)
    except Exception:
        try:
            mtp.delete_object(stage)
        except Exception:
            pass
        raise


def _remove_destination(target, remote):
    """Best-effort cleanup of a partially copied destination or the original
    a replace is about to supersede — callers re-check the outcome rather
    than trusting this to have removed anything."""
    try:
        if remote:
            files, folders = _walk_remote(target)
            for entry in files:
                mtp.delete_object(entry['path'])
            for folder in sorted(folders, key=lambda f: len(mtp.parse(f['path'])[2]), reverse=True):
                mtp.delete_object(folder['path'])
        else:
            target_path = Path(target)
            if target_path.is_dir() and not target_path.is_symlink():
                # ._ sidecars appear and vanish mid-walk on ExFAT; a single
                # rmtree pass may abort while the dir is already empty.
                shutil.rmtree(target_path, ignore_errors=True)
                try:
                    target_path.rmdir()
                except OSError:
                    pass
            else:
                target_path.unlink(missing_ok=True)
    except Exception:
        pass


def _write_item(item, target, remote, cancelled, on_file):
    """Write one source file/folder to an empty destination path."""
    is_dir = item['source'].is_dir()
    if remote:
        if is_dir:
            _ensure_remote_dir(target)
            for folder in item['folders']:
                _ensure_remote_dir(target + '/' + folder.relative_to(item['source']).as_posix())
        for entry in item['files']:
            cancelled()
            relative = entry.relative_to(item['source']).as_posix() if is_dir else item['name']
            _upload_local(entry, target + '/' + relative if is_dir else target, cancelled)
            on_file()
    elif is_dir:
        Path(target).mkdir()
        for folder in item['folders']:
            (Path(target) / folder.relative_to(item['source'])).mkdir(parents=True, exist_ok=True)
        for entry in item['files']:
            cancelled()
            (Path(target) / entry.relative_to(item['source'])).parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(entry, Path(target) / entry.relative_to(item['source']))
            on_file()
    else:
        cancelled()
        with file_lock(str(item['source'])):
            shutil.copy2(item['source'], target)
        on_file()


def _rename_into(target, staged, remote):
    if remote:
        mtp.rename_object(staged, mtp.parse(target)[2][-1])
    else:
        Path(staged).rename(target)


def _publish_new(staged, target, remote):
    """Move staged content onto a destination that must not exist yet.

    Returns False when a same-named target appeared between the earlier check
    and publishing, leaving it untouched. The final operation itself refuses
    to overwrite, so a late-arriving target is never clobbered."""
    if remote:
        if mtp.stat_file(target) is not None:
            return False
        try:
            _rename_into(target, staged, remote)
        except Exception:
            # The device may refuse the rename because a same-named object
            # appeared mid-staging; keep whatever now sits at the target.
            if mtp.stat_file(target) is not None:
                return False
            raise
        return True
    try:
        _rename_exclusive(staged, target)
    except PublishConflict:
        raise
    except FileExistsError:
        return False
    return True


def _publish_replace(target, staged, remote):
    """Publish a staged sibling over an existing target, discarding the original.

    Finder-style replace keeps no backup: the original is deleted first, then
    the staged copy claims the freed name without ever renaming over a foreign
    object that slipped into the gap — such a claimant keeps the name. When
    publishing fails after the original is already gone, the staged copy is
    left in place and reported via `error.leftover` rather than destroyed."""
    def leftovers_of(error):
        # A claim-write failure inside _rename_exclusive may already point at
        # the claimed target path — on the error itself or on its cause — so
        # merge every recorded leftover before adding the staged copy.
        merged = []
        for source in (getattr(getattr(error, '__cause__', None), 'leftover', None),
                       getattr(error, 'leftover', None)):
            if isinstance(source, str):
                merged.append(source)
            elif source:
                merged.extend(source)
        return merged + [staged]

    if remote:
        _remove_destination(target, remote)
        try:
            # MTP has no atomic swap — a foreign object that claimed the freed
            # name must not be renamed over.
            if mtp.stat_file(target) is not None:
                raise FileExistsError(errno.EEXIST, '目标已存在', target)
            _rename_into(target, staged, remote)
        except Exception as error:
            error.leftover = leftovers_of(error)
            raise
        return
    _remove_destination(target, remote)
    try:
        _rename_exclusive(staged, target)
    except Exception as error:
        error.leftover = leftovers_of(error)
        raise


def apply_add(sources, directory, remote, overwrite=False, progress=lambda *args: None,
              cancelled=lambda: None, decisions=None):
    """Copy local sources into the browsed directory.

    Same-named top-level items are skipped, or — when overwrite is set — staged,
    the original is deleted, and the staged copy then takes its name. When a
    `decisions` map is supplied it replaces `overwrite` per source: 'copy'
    expects a free target (a target that appears later is kept and reported),
    'skip' never writes, and 'replace' re-checks the approved target `stamp`
    right before publishing. The report keeps copied/replaced/skipped/failed/
    remaining/kept lists even after cancellation.
    """
    items = []
    for source in sources:
        path = Path(source)
        if path.is_dir():
            # AppleDouble sidecars (._*) materialize and vanish on their own
            # on ExFAT volumes; they are not content and must never stage.
            files = sorted(entry for entry in path.rglob('*') if entry.is_file()
                           and not any(part.startswith('._') for part in entry.relative_to(path).parts))
            folders = sorted(entry for entry in path.rglob('*') if entry.is_dir()
                             and not any(part.startswith('._') for part in entry.relative_to(path).parts))
        else:
            files, folders = [path], []
        items.append({'source': path, 'name': path.name, 'files': files, 'folders': folders})
    total = sum(len(item['files']) for item in items)
    result = {'copied': [], 'replaced': [], 'skipped': [], 'failed': [], 'remaining': [],
              'leftovers': [], 'kept': [], 'destination': directory}
    done = 0
    for index, item in enumerate(items):
        name = item['name']
        target = mtp.join(directory, name) if remote else str(Path(directory) / name)
        exists = mtp.stat_file(target) is not None if remote else (Path(target).exists() or Path(target).is_symlink())
        decision = (decisions or {}).get(str(item['source'])) or {}
        mode = decision.get('action') or ('replace' if overwrite else 'copy')
        stamp = decision.get('stamp')
        if mode == 'skip':
            result['skipped'].append(name)
            done += len(item['files'])
            continue
        if exists:
            if mode != 'replace':
                # The preview promised a free target; one exists now, so the
                # unapproved original stays untouched and is reported as kept.
                if decisions is None:
                    result['skipped'].append(name)
                else:
                    result['kept'].append({'name': name, 'reason': 'appeared'})
                done += len(item['files'])
                continue
        staged = target + '.plcn-' + uuid.uuid4().hex

        def note_file():
            nonlocal done
            done += 1
            progress(done, total, '复制：' + name)

        try:
            _write_item(item, staged, remote, cancelled, note_file)
            if exists:
                cancelled()
                verdict = None
                if decisions is not None:
                    if stamp is None:
                        # Approved replace but nothing reliable to re-check against.
                        verdict = 'unverifiable'
                    else:
                        try:
                            refreshed = target_stamp(target, remote)
                            if not _stamp_verifiable(refreshed):
                                verdict = 'unverifiable'
                            elif refreshed != stamp:
                                # The approved target changed during staging; keep both.
                                verdict = 'changed'
                        except Exception:
                            verdict = 'unverifiable'
                if verdict:
                    _remove_destination(staged, remote)
                    result['kept'].append({'name': name, 'reason': verdict})
                    continue
                _publish_replace(target, staged, remote)
                result['replaced'].append(name)
            elif _publish_new(staged, target, remote):
                result['copied'].append(name)
            else:
                _remove_destination(staged, remote)
                if decisions is not None:
                    result['kept'].append({'name': name, 'reason': 'appeared'})
                else:
                    result['skipped'].append(name)
        except TaskCancelled as error:
            _remove_destination(staged, remote)
            result['failed'].append({'name': name, 'error': str(error)})
            result['remaining'] = [pending['name'] for pending in items[index + 1:]]
            return result
        except (PublishConflict, PublishAbort) as error:
            # When the staged copy is the survivor being reported (publish
            # failed after the original was deleted), it stays in place.
            leftovers = []
            for source in (getattr(error.__cause__, 'leftover', None),
                           getattr(error, 'leftover', None)):
                if isinstance(source, str):
                    leftovers.append(source)
                elif source:
                    leftovers.extend(source)
            leftovers = list(dict.fromkeys(leftovers))
            if staged not in leftovers:
                _remove_destination(staged, remote)
            entry = {'name': name, 'target': target,
                     'error': str(error) + ('：' + str(error.__cause__)
                                            if error.__cause__ is not None
                                            and not isinstance(error, PublishConflict) else ''),
                     'written': [str(Path(target) / member) for member in error.written]}
            if isinstance(error, PublishConflict):
                entry['conflict'] = str(error.member)
            else:
                entry['interrupted'] = str(error.member)
            for path in leftovers:
                result['leftovers'].append({'name': name, 'path': path,
                                            'reason': '写入中断，路径待核对'})
            result['failed'].append(entry)
            result['remaining'] = [pending['name'] for pending in items[index + 1:]]
            return result
        except Exception as error:
            # Same survivor rule: a staged copy reported via `error.leftover`
            # is evidence, not residue to clean up.
            leftovers = []
            for source in (getattr(error.__cause__, 'leftover', None),
                           getattr(error, 'leftover', None)):
                if isinstance(source, str):
                    leftovers.append(source)
                elif source:
                    leftovers.extend(source)
            leftovers = list(dict.fromkeys(leftovers))
            if staged not in leftovers:
                _remove_destination(staged, remote)
            result['failed'].append({'name': name, 'error': str(error), 'target': target})
            for path in leftovers:
                result['leftovers'].append({'name': name, 'path': path,
                                            'reason': '写入中断，路径待核对'})
            result['remaining'] = [pending['name'] for pending in items[index + 1:]]
            return result
    return result
