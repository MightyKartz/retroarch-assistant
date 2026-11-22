import json
import os
import shutil
from pathlib import Path

import pytest

import game_file_actions as ops
import game_file_sync as sync


def library(tmp_path, items):
    root = tmp_path / 'RetroArch'
    playlists = root / 'playlists'
    playlists.mkdir(parents=True)
    target = playlists / 'GBA.lpl'
    target.write_text(json.dumps({'items': items}), encoding='utf-8')
    return root, target


def test_rename_copy_and_delete_work_on_any_local_file_and_folder(tmp_path, monkeypatch):
    monkeypatch.setenv('RETROARCH_ASSISTANT_HOME', str(tmp_path / 'home'))
    root = tmp_path / 'RetroArch'
    directory = tmp_path / 'Roms'
    directory.mkdir()
    backup_file = directory / 'game.md.bak'
    backup_file.write_bytes(b'backup')
    folder = directory / 'Folder'
    folder.mkdir()
    (folder / 'a.md').write_bytes(b'a')
    path, name, remote = ops.validate_op({'action': 'rename', 'path': str(backup_file), 'root': str(root), 'name': 'renamed.bak'})
    assert ops.apply_rename(path, name, remote)['new_path'] == str(directory / 'renamed.bak')
    path, target, remote = ops.validate_op({'action': 'copy', 'path': str(directory / 'renamed.bak'),
                                          'root': str(root), 'directory': str(directory)})
    copied = ops.apply_copy(path, target, remote)
    assert copied['path'] == str(directory / 'renamed 副本.bak') and copied['name'] == 'renamed 副本.bak'
    path, _, remote = ops.validate_op({'action': 'delete', 'path': str(folder), 'root': str(root)})
    deleted = ops.apply_delete(path, remote)
    assert deleted['path'] == str(folder) and not deleted['cancelled'] and not folder.exists()


def test_file_ops_reject_device_boundary_and_bad_names(tmp_path):
    root = 'mtp://device/00010001/RetroArch'
    with pytest.raises(ValueError):
        ops.validate_op({'action': 'delete', 'path': 'mtp://other/00010001/Roms/a.md', 'root': root})
    for name in ('', '..', 'a/b', 'a\\b'):
        with pytest.raises(ValueError):
            ops.validate_op({'action': 'rename', 'path': str(tmp_path / 'x'), 'root': str(tmp_path), 'name': name})
    with pytest.raises(ValueError):
        ops.validate_op({'action': 'unknown', 'path': str(tmp_path / 'x'), 'root': str(tmp_path)})


def test_add_copies_files_and_folders_and_skips_existing(tmp_path):
    destination = tmp_path / 'Roms' / 'SFC'
    destination.mkdir(parents=True)
    (destination / 'existing.sfc').write_bytes(b'old')
    source_dir = tmp_path / 'incoming'
    game_folder = source_dir / 'Game'
    (game_folder / 'disc').mkdir(parents=True)
    (game_folder / 'disc' / 'game.cue').write_bytes(b'cue')
    (game_folder / 'disc' / 'game.bin').write_bytes(b'bin')
    rom = source_dir / 'new.sfc'
    rom.write_bytes(b'new')
    clash = source_dir / 'existing.sfc'
    clash.write_bytes(b'clash')
    sources, directory, remote, overwrite = ops.validate_add({
        'action': 'add', 'sources': [str(rom), str(game_folder), str(clash)],
        'directory': str(destination), 'root': str(tmp_path)})
    assert overwrite is False
    result = ops.apply_add(sources, directory, remote, overwrite)
    assert result['copied'] == ['new.sfc', 'Game']
    assert result['skipped'] == ['existing.sfc']
    assert result['failed'] == [] and result['remaining'] == []
    assert (destination / 'new.sfc').read_bytes() == b'new'
    assert (destination / 'Game' / 'disc' / 'game.cue').read_bytes() == b'cue'
    assert (destination / 'existing.sfc').read_bytes() == b'old'


def test_add_overwrite_stages_then_replaces(tmp_path, monkeypatch):
    monkeypatch.setenv('RETROARCH_ASSISTANT_HOME', str(tmp_path / 'home'))
    destination = tmp_path / 'Roms'
    destination.mkdir()
    (destination / 'game.sfc').write_bytes(b'old version')
    folder = destination / 'Game'
    folder.mkdir()
    (folder / 'a.bin').write_bytes(b'old bin')
    source_dir = tmp_path / 'incoming'
    source_dir.mkdir()
    (source_dir / 'game.sfc').write_bytes(b'new version')
    new_folder = source_dir / 'Game'
    new_folder.mkdir()
    (new_folder / 'b.bin').write_bytes(b'new bin')
    _, _, _, overwrite = ops.validate_add({
        'action': 'add', 'sources': [str(source_dir / 'game.sfc'), str(new_folder)],
        'directory': str(destination), 'root': str(tmp_path), 'overwrite': True})
    assert overwrite is True
    result = ops.apply_add([str(source_dir / 'game.sfc'), str(new_folder)], str(destination), False, True)
    assert result['replaced'] == ['game.sfc', 'Game']
    assert result['skipped'] == [] and result['failed'] == []
    assert (destination / 'game.sfc').read_bytes() == b'new version'
    assert (destination / 'Game' / 'b.bin').read_bytes() == b'new bin'
    assert not (destination / 'Game' / 'a.bin').exists()
    assert not (tmp_path / 'home' / 'file-trash').exists()


def test_add_reports_failure_and_remaining_and_cleans_partial(tmp_path):
    destination = tmp_path / 'dest'
    destination.mkdir()
    folder = tmp_path / 'src' / 'big'
    folder.mkdir(parents=True)
    (folder / 'a.bin').write_bytes(b'a')
    (folder / 'b.bin').write_bytes(b'b')
    later = tmp_path / 'src' / 'later.sfc'
    later.write_bytes(b'later')
    sources = [str(folder), str(later)]
    calls = iter([None, RuntimeError('disk full')])
    real_copy2 = ops.shutil.copy2

    def fake_copy2(*args, **kwargs):
        outcome = next(calls)
        if isinstance(outcome, Exception):
            raise outcome
        return real_copy2(*args, **kwargs)

    ops.shutil.copy2 = fake_copy2
    try:
        result = ops.apply_add(sources, str(destination), False)
    finally:
        ops.shutil.copy2 = real_copy2
    failure = result['failed'][0]
    assert failure['name'] == 'big' and failure['error'] == 'disk full'
    assert failure['target'] == str(destination / 'big')
    assert result['remaining'] == ['later.sfc']
    assert result['copied'] == []
    assert not (destination / 'big').exists()


def test_validate_add_boundaries(tmp_path):
    with pytest.raises(ValueError):
        ops.validate_add({'action': 'add', 'sources': ['/nonexistent-file'], 'directory': str(tmp_path), 'root': ''})
    with pytest.raises(ValueError):
        ops.validate_add({'action': 'add', 'sources': [str(tmp_path)], 'directory': 'mtp://dev/0001/Roms', 'root': 'mtp://other/0001/Lib'})
    with pytest.raises(ValueError):
        ops.validate_add({'action': 'add', 'sources': ['mtp://dev/0001/a'], 'directory': str(tmp_path), 'root': ''})


def test_sync_renames_paths_removes_entries_and_reports_unmatched(tmp_path):
    roms = tmp_path / 'Roms' / 'GBA'
    root, target = library(tmp_path, [
        {'path': str(roms / 'a.gba'), 'label': 'A'},
        {'path': str(roms / 'old.gba'), 'label': 'B'},
        {'path': str(roms / 'sub' / 'c.gba'), 'label': 'C'},
        {'path': str(roms / 'pack.zip#inner.gba'), 'label': 'D'},
    ])
    changes = [
        {'action': 'rename', 'path': str(roms / 'old.gba'), 'to': str(roms / 'new.gba'), 'folder': False},
        {'action': 'rename', 'path': str(roms / 'pack.zip'), 'to': str(roms / 'pack2.zip'), 'folder': False},
        {'action': 'delete', 'path': str(roms / 'sub'), 'folder': True},
        {'action': 'delete', 'path': str(roms / 'ghost.gba'), 'folder': False},
    ]
    plan = sync.preview(str(root), changes)
    public = plan['public']
    assert [entry['label'] for entry in public['renamed']] == ['B', 'D']
    assert public['renamed'][1]['new'] == str(roms / 'pack2.zip') + '#inner.gba'
    assert [entry['label'] for entry in public['removed']] == ['C']
    assert public['unmatched'] == [str(roms / 'ghost.gba')]
    sync.apply(str(root), changes, public['signature'])
    items = json.loads(target.read_text(encoding='utf-8'))['items']
    assert [item['label'] for item in items] == ['A', 'B', 'D']
    assert items[1]['path'] == str(roms / 'new.gba')
    assert items[2]['path'] == str(roms / 'pack2.zip') + '#inner.gba'


def test_sync_rejects_a_stale_signature(tmp_path):
    root, target = library(tmp_path, [{'path': str(tmp_path / 'x.gba'), 'label': 'X'}])
    changes = [{'action': 'delete', 'path': str(tmp_path / 'x.gba'), 'folder': False}]
    signature = sync.preview(str(root), changes)['public']['signature']
    target.write_text(json.dumps({'items': []}), encoding='utf-8')
    with pytest.raises(ValueError, match='已变化'):
        sync.apply(str(root), changes, signature)


def test_replace_publish_failure_preserves_original_file(tmp_path, monkeypatch):
    monkeypatch.setenv('RETROARCH_ASSISTANT_HOME', str(tmp_path / 'home'))
    destination = tmp_path / 'Roms'
    destination.mkdir()
    original = destination / 'game.sfc'
    original.write_bytes(b'old version')
    source = tmp_path / 'incoming'
    source.mkdir()
    (source / 'game.sfc').write_bytes(b'new version')
    real_exclusive = ops._rename_exclusive
    calls = []

    def fail_publish(staged, target):
        calls.append(staged)
        if len(calls) == 1:
            raise OSError('publish failed')
        return real_exclusive(staged, target)
    monkeypatch.setattr(ops, '_rename_exclusive', fail_publish)
    result = ops.apply_add([str(source / 'game.sfc')], str(destination), False, overwrite=True)
    monkeypatch.setattr(ops, '_rename_exclusive', real_exclusive)
    assert result['failed'] and 'publish failed' in result['failed'][0]['error']
    assert result['replaced'] == []
    assert original.read_bytes() == b'old version'
    assert not list(destination.glob('*.plcn-*'))


def test_replace_folder_publish_failure_rolls_back_original(tmp_path, monkeypatch):
    monkeypatch.setenv('RETROARCH_ASSISTANT_HOME', str(tmp_path / 'home'))
    destination = tmp_path / 'Roms'
    destination.mkdir()
    folder = destination / 'Game'
    folder.mkdir()
    (folder / 'a.bin').write_bytes(b'old bin')
    source = tmp_path / 'incoming'
    source.mkdir()
    (source / 'Game').mkdir()
    (source / 'Game' / 'b.bin').write_bytes(b'new bin')
    real_exclusive = ops._rename_exclusive
    calls = []

    def fail_publish(staged, target):
        calls.append(staged)
        if len(calls) == 1:
            raise OSError('device refused rename')
        return real_exclusive(staged, target)
    monkeypatch.setattr(ops, '_rename_exclusive', fail_publish)
    result = ops.apply_add([str(source / 'Game')], str(destination), False, overwrite=True)
    monkeypatch.setattr(ops, '_rename_exclusive', real_exclusive)
    assert result['failed'] and 'refused' in result['failed'][0]['error']
    assert (folder / 'a.bin').read_bytes() == b'old bin'
    assert not list(destination.glob('*.plcn-*'))


def test_replace_failure_reports_recoverable_paths_when_rollback_fails(tmp_path, monkeypatch):
    monkeypatch.setenv('RETROARCH_ASSISTANT_HOME', str(tmp_path / 'home'))
    destination = tmp_path / 'Roms'
    destination.mkdir()
    folder = destination / 'Game'
    folder.mkdir()
    (folder / 'a.bin').write_bytes(b'old bin')
    source = tmp_path / 'incoming'
    source.mkdir()
    (source / 'Game').mkdir()
    (source / 'Game' / 'b.bin').write_bytes(b'new bin')
    # Both the forward publish and the restore claim refuse their target.
    monkeypatch.setattr(ops, '_rename_exclusive',
                        lambda *a: (_ for _ in ()).throw(OSError('publish failed')))
    result = ops.apply_add([str(source / 'Game')], str(destination), False, overwrite=True)
    entry = result['failed'][0]
    assert '.plcn-old-' in entry['error'] and '.plcn-' in entry['error']
    # Both the parked original and the staged copy remain readable on disk.
    assert len(entry['recoverable']) == 2
    parked_dir = next(Path(path) for path in entry['recoverable'] if '.plcn-old-' in path)
    staged_dir = next(Path(path) for path in entry['recoverable'] if '.plcn-old-' not in path)
    assert (parked_dir / 'a.bin').read_bytes() == b'old bin'
    assert (staged_dir / 'b.bin').read_bytes() == b'new bin'


def test_local_delete_and_copy_respect_cancellation(tmp_path):
    folder = tmp_path / 'Game'
    folder.mkdir()
    (folder / 'a.bin').write_bytes(b'a')
    (folder / 'b.bin').write_bytes(b'b')
    from task_control import TaskCancelled

    def cancelled():
        raise TaskCancelled('cancelled')
    deleted = ops.apply_delete(str(folder), False, cancelled=cancelled)
    assert deleted['cancelled'] and deleted['deleted'] == []
    assert (folder / 'a.bin').exists() and (folder / 'b.bin').exists()

    target = tmp_path / 'dest'
    target.mkdir()
    with pytest.raises(TaskCancelled):
        ops.apply_copy(str(folder), str(target), False, cancelled=cancelled)
    assert not (target / 'Game').exists()


def test_local_delete_can_stop_mid_folder(tmp_path):
    folder = tmp_path / 'Game'
    folder.mkdir()
    for name in ('a.bin', 'b.bin', 'c.bin'):
        (folder / name).write_bytes(name.encode())
    from task_control import TaskCancelled
    calls = [0]

    def cancelled():
        calls[0] += 1
        if calls[0] > 2:
            raise TaskCancelled('cancelled')
    deleted = ops.apply_delete(str(folder), False, cancelled=cancelled)
    assert deleted['cancelled']
    remaining = list(folder.glob('*.bin'))
    assert 0 < len(remaining) < 3 and folder.exists()
    assert {Path(path).name for path in deleted['deleted']} == {'a.bin', 'b.bin', 'c.bin'} - {entry.name for entry in remaining}


def test_local_delete_returns_partial_result_on_io_error(tmp_path, monkeypatch):
    folder = tmp_path / 'Game'
    folder.mkdir()
    for name in ('a.bin', 'b.bin'):
        (folder / name).write_bytes(name.encode())
    real_unlink = Path.unlink
    calls = [0]
    def failing(path, *args, **kwargs):
        calls[0] += 1
        if calls[0] > 1:
            raise OSError('设备 I/O 错误')
        return real_unlink(path, *args, **kwargs)
    monkeypatch.setattr(Path, 'unlink', failing)
    result = ops.apply_delete(str(folder), False)
    assert len(result['deleted']) == 1 and not result['cancelled']
    assert 'I/O' in result['error']
    survivors = list(folder.glob('*.bin'))
    assert len(survivors) == 1 and str(survivors[0]) not in result['deleted']

    solo = tmp_path / 'solo.bin'
    solo.write_bytes(b'x')
    monkeypatch.setattr(Path, 'unlink', lambda *a, **k: (_ for _ in ()).throw(OSError('io')))
    with pytest.raises(OSError):
        ops.apply_delete(str(solo), False)


def test_completed_result_with_cancelled_flag_marks_job_cancelled():
    from job_manager import JobManager
    jobs = JobManager()
    ident = jobs.create_job()
    jobs.complete_job(ident, {'path': '/x', 'deleted': ['/x/a'], 'cancelled': True})
    assert jobs.jobs[ident]['status'] == 'cancelled' and jobs.jobs[ident]['result']['deleted'] == ['/x/a']
    other = jobs.create_job()
    jobs.complete_job(other, {'path': '/y', 'deleted': ['/y'], 'cancelled': False})
    assert jobs.jobs[other]['status'] == 'completed'


def test_cancelled_batch_progress_reports_processed_count():
    from job_manager import JobManager
    jobs = JobManager()
    ident = jobs.create_job()
    jobs.update_job(ident, 0, 3, 'start')
    jobs.complete_job(ident, {'processed_count': 1, 'total_count': 3, 'applied_count': 5, 'cancelled': True})
    job = jobs.jobs[ident]
    assert job['status'] == 'cancelled' and job['progress'] == 1 and job['total'] == 3
    # Cancelling before the first progress update still adopts the batch total.
    early = jobs.create_job()
    jobs.complete_job(early, {'processed_count': 0, 'total_count': 3, 'cancelled': True})
    assert jobs.jobs[early]['status'] == 'cancelled' and jobs.jobs[early]['progress'] == 0 and jobs.jobs[early]['total'] == 3


def test_local_replace_reports_parked_leftover_when_cleanup_partially_fails(tmp_path, monkeypatch):
    source = tmp_path / 'new' / 'Game'
    source.mkdir(parents=True)
    (source / 'a.txt').write_text('new-a')
    (source / 'b.txt').write_text('new-b')
    destination = tmp_path / 'dest'
    target = destination / 'Game'
    target.mkdir(parents=True)
    (target / 'old1.txt').write_text('x')
    (target / 'old2.txt').write_text('y')
    real_unlink = Path.unlink
    calls = [0]
    def flaky(path, *args, **kwargs):
        if '.plcn-old-' in str(path):
            calls[0] += 1
            if calls[0] > 1:
                raise OSError('io error')
        return real_unlink(path, *args, **kwargs)
    monkeypatch.setattr(Path, 'unlink', flaky)
    result = ops.apply_add([str(source)], str(destination), False, overwrite=True)
    parked = [entry for entry in destination.iterdir() if '.plcn-old-' in entry.name and not entry.name.endswith('.plcn-lock')]
    assert parked, 'parked original should remain on disk'
    assert [item['path'] for item in result['leftovers']] == [str(parked[0])]
    assert (target / 'a.txt').read_text() == 'new-a' and (target / 'b.txt').read_text() == 'new-b'


def test_copy_paste_uses_host_platform_source_name(tmp_path):
    source = tmp_path / 'src'
    source.mkdir()
    (source / 'game.nes').write_bytes(b'rom')
    target = tmp_path / 'dest'
    target.mkdir()
    copied = ops.apply_copy(str(source / 'game.nes'), str(target), False)
    assert copied['path'] == str(target / 'game.nes') and copied['name'] == 'game.nes'
    assert not list(source.glob('*副本*'))


def test_mtp_replace_moves_original_aside_and_keeps_it(tmp_path, monkeypatch):
    import mtp_runtime as mtp
    root_uri = mtp.uri('dev', 0x10001, 'Roms')
    files = {mtp.join(root_uri, 'game.sfc'): b'old version'}

    def entry(uri):
        return {'id': 1, 'folder': False, 'size': len(files[uri])}
    monkeypatch.setattr(mtp, 'stat_file', lambda path: entry(path) if path in files else None)
    monkeypatch.setattr(mtp, 'listing', lambda path: [])
    uploads = {}

    def upload(target, source, expected, stage, cancelled=lambda: None):
        uploads[target] = Path(source).read_bytes()
        files[target] = uploads[target]
    monkeypatch.setattr(mtp, 'upload_file', upload)
    monkeypatch.setattr(mtp, 'rename_object', lambda path, name: (
        files.__setitem__(path.rsplit('/', 1)[0] + '/' + name, files.pop(path)),
        path.rsplit('/', 1)[0] + '/' + name)[1])
    monkeypatch.setattr(mtp, 'delete_object', lambda path: files.pop(path, None))

    source = tmp_path / 'game.sfc'
    source.write_bytes(b'new version')
    result = ops.apply_add([str(source)], root_uri, True, overwrite=True)
    assert result['replaced'] == ['game.sfc']
    assert files[mtp.join(root_uri, 'game.sfc')] == b'new version'
    # The parked original is retained and reported — device writes cannot be
    # excluded during a delete window, so it is never removed by name.
    parked = [entry['path'] for entry in result['leftovers'] if '.plcn-old-' in entry['path']]
    assert len(parked) == 1
    assert files[parked[0]] == b'old version'


def test_mtp_replace_reports_leftover_when_cleanup_fails(tmp_path, monkeypatch):
    import mtp_runtime as mtp
    root_uri = mtp.uri('dev', 0x10001, 'Roms')
    files = {mtp.join(root_uri, 'game.sfc'): b'old version'}
    monkeypatch.setattr(mtp, 'stat_file', lambda path: {'id': 1, 'folder': False, 'size': 1} if path in files else None)
    monkeypatch.setattr(mtp, 'listing', lambda path: [])
    monkeypatch.setattr(mtp, 'upload_file', lambda target, source, expected, stage, cancelled=lambda: None: files.__setitem__(target, Path(source).read_bytes()))
    monkeypatch.setattr(mtp, 'rename_object', lambda path, name: (
        files.__setitem__(path.rsplit('/', 1)[0] + '/' + name, files.pop(path)),
        path.rsplit('/', 1)[0] + '/' + name)[1])
    monkeypatch.setattr(mtp, 'delete_object', lambda path: (_ for _ in ()).throw(RuntimeError('delete refused')))

    source = tmp_path / 'game.sfc'
    source.write_bytes(b'new version')
    result = ops.apply_add([str(source)], root_uri, True, overwrite=True)
    assert result['replaced'] == ['game.sfc']
    assert result['leftovers'] and '.plcn-old-' in result['leftovers'][0]['path']
    assert files[mtp.join(root_uri, 'game.sfc')] == b'new version'
    assert files[result['leftovers'][0]['path']] == b'old version'


def test_mtp_copy_decodes_percent_encoded_source_name(tmp_path, monkeypatch):
    import mtp_runtime as mtp
    source_uri = mtp.uri('dev', 0x10001, 'Roms/仙剑 1.cue')
    dest_uri = mtp.uri('dev', 0x10001, 'Target')
    files = {source_uri: b'cue content'}
    monkeypatch.setattr(mtp, 'stat_file', lambda path: {'id': 1, 'folder': False, 'size': 1} if path in files else None)

    def download(path, target, cancelled=lambda: None):
        Path(target).write_bytes(files[path])
    monkeypatch.setattr(mtp, 'download_file', download)
    written = {}
    monkeypatch.setattr(mtp, 'upload_file', lambda target, source, expected, stage, cancelled=lambda: None: (
        written.__setitem__(target, Path(source).read_bytes()), files.__setitem__(target, Path(source).read_bytes()))[0])

    copied = ops.apply_copy(source_uri, dest_uri, True)
    expected_target = mtp.join(dest_uri, '仙剑 1.cue')
    assert copied['path'] == expected_target and copied['name'] == '仙剑 1.cue'
    assert mtp.parse(copied['path'])[2][-1] == '仙剑 1.cue'
    assert written == {expected_target: b'cue content'}


def test_sync_chains_sequential_renames_and_renamed_then_deleted(tmp_path):
    roms = tmp_path / 'Roms' / 'NES'
    roms.mkdir(parents=True)
    root, target = library(tmp_path, [
        {'path': str(roms / 'A.nes'), 'label': 'chain'},
        {'path': str(roms / 'gone.nes'), 'label': 'renamed-then-deleted'},
        {'path': str(roms / 'pack.zip#inner.nes'), 'label': 'archive'},
        {'path': str(roms / 'keep.nes'), 'label': 'untouched'},
    ])
    changes = [
        {'action': 'rename', 'path': str(roms / 'A.nes'), 'to': str(roms / 'B.nes'), 'folder': False},
        {'action': 'rename', 'path': str(roms / 'B.nes'), 'to': str(roms / 'C.nes'), 'folder': False},
        {'action': 'rename', 'path': str(roms / 'gone.nes'), 'to': str(roms / 'gone2.nes'), 'folder': False},
        {'action': 'delete', 'path': str(roms / 'gone2.nes'), 'folder': False},
        {'action': 'rename', 'path': str(roms / 'pack.zip'), 'to': str(roms / 'pack2.zip'), 'folder': False},
    ]
    plan = sync.preview(str(root), changes)
    public = plan['public']
    renamed = {entry['label']: entry['new'] for entry in public['renamed']}
    assert renamed['chain'] == str(roms / 'C.nes')
    assert renamed['archive'] == str(roms / 'pack2.zip') + '#inner.nes'
    assert [entry['label'] for entry in public['removed']] == ['renamed-then-deleted']
    sync.apply(str(root), changes, public['signature'])
    items = {item['label']: item['path'] for item in json.loads(target.read_text(encoding='utf-8'))['items']}
    assert items['chain'] == str(roms / 'C.nes')
    assert 'renamed-then-deleted' not in items
    assert items['archive'] == str(roms / 'pack2.zip') + '#inner.nes'
    assert items['untouched'] == str(roms / 'keep.nes')


def test_sync_dir_rename_then_file_rename_inside_chains(tmp_path):
    roms = tmp_path / 'Roms'
    folder = roms / 'Disc'
    folder.mkdir(parents=True)
    root, target = library(tmp_path, [{'path': str(folder / 'game.cue'), 'label': 'cue'}])
    changes = [
        {'action': 'rename', 'path': str(folder), 'to': str(roms / 'Disc Renamed'), 'folder': True},
        {'action': 'rename', 'path': str(roms / 'Disc Renamed' / 'game.cue'), 'to': str(roms / 'Disc Renamed' / 'game2.cue'), 'folder': False},
    ]
    public = sync.preview(str(root), changes)['public']
    assert public['renamed'][0]['new'] == str(roms / 'Disc Renamed' / 'game2.cue')


def test_sync_broken_playlist_does_not_block_valid_ones(tmp_path):
    roms = tmp_path / 'Roms'
    roms.mkdir()
    root, target = library(tmp_path, [{'path': str(roms / 'a.nes'), 'label': 'A'}])
    broken = root / 'playlists' / 'Broken.lpl'
    broken.write_text('{invalid json', encoding='utf-8')
    broken_bytes = broken.read_bytes()
    changes = [{'action': 'rename', 'path': str(roms / 'a.nes'), 'to': str(roms / 'b.nes'), 'folder': False}]
    plan = sync.preview(str(root), changes)
    public = plan['public']
    assert [entry['label'] for entry in public['renamed']] == ['A']
    assert [entry['playlist'] for entry in public['failed']] == ['Broken.lpl']
    sync.apply(str(root), changes, public['signature'])
    assert json.loads(target.read_text(encoding='utf-8'))['items'][0]['path'] == str(roms / 'b.nes')
    assert broken.read_bytes() == broken_bytes


def test_sync_isolates_a_single_transport_read_failure(tmp_path, monkeypatch):
    roms = tmp_path / 'Roms'
    roms.mkdir()
    root, target = library(tmp_path, [{'path': str(roms / 'a.nes'), 'label': 'A'}])
    extra = root / 'playlists' / 'Extra.lpl'
    extra.write_text(json.dumps({'items': [{'path': '/p.chd', 'label': 'P'}]}), encoding='utf-8')
    extra_bytes = extra.read_bytes()
    real_read = sync.scan.read
    def flaky(path):
        if Path(path).name == 'Extra.lpl':
            raise RuntimeError('MTP 传输中断')
        return real_read(path)
    monkeypatch.setattr(sync.scan, 'read', flaky)
    changes = [{'action': 'rename', 'path': str(roms / 'a.nes'), 'to': str(roms / 'b.nes'), 'folder': False}]
    plan = sync.preview(str(root), changes)
    public = plan['public']
    assert [entry['label'] for entry in public['renamed']] == ['A']
    assert [entry['playlist'] for entry in public['failed']] == ['Extra.lpl']
    assert 'MTP' in public['failed'][0]['error']
    assert public['playlists_total'] == 2 and public['playlists_read'] == 1
    sync.apply(str(root), changes, public['signature'])
    assert json.loads(target.read_text(encoding='utf-8'))['items'][0]['path'] == str(roms / 'b.nes')
    assert extra.read_bytes() == extra_bytes


def test_sync_reports_all_playlists_broken(tmp_path):
    root = tmp_path / 'RetroArch'
    (root / 'playlists').mkdir(parents=True)
    (root / 'playlists' / 'Broken.lpl').write_text('{bad', encoding='utf-8')
    plan = sync.preview(str(root), [{'action': 'rename', 'path': '/x.nes', 'to': '/y.nes', 'folder': False}])
    public = plan['public']
    assert public['failed'] and not public['renamed'] and not plan['writes']
    assert public['playlists_total'] == 1 and public['playlists_read'] == 0


@pytest.mark.parametrize('payload', ['{"items": null}', '{"items": [null]}', '{"items": [{"path": null}]}'])
def test_sync_structurally_broken_playlist_does_not_block_valid_ones(tmp_path, payload):
    roms = tmp_path / 'Roms'
    roms.mkdir()
    root, target = library(tmp_path, [{'path': str(roms / 'a.nes'), 'label': 'A'}])
    broken = root / 'playlists' / 'Broken.lpl'
    broken.write_text(payload, encoding='utf-8')
    broken_bytes = broken.read_bytes()
    changes = [{'action': 'rename', 'path': str(roms / 'a.nes'), 'to': str(roms / 'b.nes'), 'folder': False}]
    plan = sync.preview(str(root), changes)
    public = plan['public']
    assert [entry['label'] for entry in public['renamed']] == ['A']
    assert [entry['playlist'] for entry in public['failed']] == ['Broken.lpl']
    assert public['playlists_total'] == 2 and public['playlists_read'] == 1
    sync.apply(str(root), changes, public['signature'])
    assert json.loads(target.read_text(encoding='utf-8'))['items'][0]['path'] == str(roms / 'b.nes')
    assert broken.read_bytes() == broken_bytes


def test_sync_zero_match_distinguishes_unreadable_from_unmatched(tmp_path):
    root, _ = library(tmp_path, [{'path': '/elsewhere.nes', 'label': 'far'}])
    broken = root / 'playlists' / 'Broken.lpl'
    broken.write_text('{invalid', encoding='utf-8')
    changes = [{'action': 'rename', 'path': '/nothing/here.nes', 'to': '/there.nes', 'folder': False}]
    public = sync.preview(str(root), changes)['public']
    assert not public['renamed'] and not public['removed'] and public['unmatched']
    assert [entry['playlist'] for entry in public['failed']] == ['Broken.lpl']
    assert public['playlists_read'] == 1 and public['playlists_total'] == 2


def _sync_changes(batch_result):
    """Mirror the page's sync registration for a batch delete result."""
    changes = []
    for entry in batch_result['items']:
        if entry['status'] == 'deleted':
            changes.append({'action': 'delete', 'path': entry['path'], 'folder': entry['folder']})
        elif entry['status'] == 'partial':
            changes += [{'action': 'delete', 'path': path, 'folder': False} for path in entry['deleted']]
    return changes


def test_validate_ops_normalizes_dedupes_and_checks_every_path(tmp_path):
    directory = tmp_path / 'Roms'
    directory.mkdir()
    one, two = directory / 'a.sfc', directory / 'b.sfc'
    one.write_bytes(b'a'); two.write_bytes(b'b')
    paths, argument, remote = ops.validate_ops({'action': 'copy',
        'paths': [str(one), str(one) + '/', str(two)], 'directory': str(directory), 'root': str(tmp_path)})
    assert paths == [str(one), str(two)] and argument == str(directory) and remote is False
    paths, _, remote = ops.validate_ops({'action': 'delete', 'paths': [str(one)], 'root': str(tmp_path)})
    assert remote is False
    with pytest.raises(ValueError):
        ops.validate_ops({'action': 'rename', 'paths': [str(one)], 'root': str(tmp_path)})
    with pytest.raises(ValueError):
        ops.validate_ops({'action': 'delete', 'paths': [], 'root': str(tmp_path)})
    with pytest.raises(ValueError):
        ops.validate_ops({'action': 'delete', 'paths': ['relative.sfc'], 'root': str(tmp_path)})
    with pytest.raises(ValueError):
        ops.validate_ops({'action': 'delete', 'paths': ['mtp://dev/0001/Roms/a', str(one)],
                          'root': 'mtp://dev/0001/Lib'})
    with pytest.raises(ValueError):
        ops.validate_ops({'action': 'delete', 'paths': ['mtp://other/0001/Roms/a'],
                          'root': 'mtp://dev/0001/Lib'})
    with pytest.raises(ValueError):
        ops.validate_ops({'action': 'copy', 'paths': [str(directory)], 'directory': str(one), 'root': str(tmp_path)})


def test_copy_batch_rejects_destination_inside_a_selected_folder(tmp_path):
    folder = tmp_path / 'Game'
    inside = folder / 'sub'
    inside.mkdir(parents=True)
    with pytest.raises(ValueError):
        ops.validate_ops({'action': 'copy', 'paths': [str(folder)], 'directory': str(inside), 'root': str(tmp_path)})


def test_copy_batch_reports_copied_failed_and_pending(tmp_path):
    source = tmp_path / 'src'
    source.mkdir()
    for name in ('a.sfc', 'c.sfc'):
        (source / name).write_bytes(name.encode())
    dest = tmp_path / 'dest'
    dest.mkdir()
    (dest / 'a.sfc').write_bytes(b'old')
    result = ops.apply_copy_batch([str(source / 'a.sfc'), str(source / 'gone.sfc'), str(source / 'c.sfc')],
                                  str(dest), False)
    assert not result['cancelled']
    assert [item['status'] for item in result['items']] == ['copied', 'failed', 'pending']
    assert result['items'][0]['name'] == 'a 副本.sfc'
    assert (dest / 'a 副本.sfc').read_bytes() == b'a.sfc'
    assert 'gone.sfc' in result['items'][1]['path']
    assert not (dest / 'c.sfc').exists()


def test_copy_batch_marks_pending_after_cancel(tmp_path):
    from task_control import TaskCancelled
    source = tmp_path / 'src'
    source.mkdir()
    for name in ('a.sfc', 'b.sfc', 'c.sfc'):
        (source / name).write_bytes(name.encode())
    dest = tmp_path / 'dest'
    dest.mkdir()
    calls = [0]

    def cancelled():
        calls[0] += 1
        if calls[0] > 1:
            raise TaskCancelled('stop')

    result = ops.apply_copy_batch([str(source / 'a.sfc'), str(source / 'b.sfc'), str(source / 'c.sfc')],
                                  str(dest), False, cancelled=cancelled)
    assert result['cancelled']
    assert [item['status'] for item in result['items']] == ['copied', 'failed', 'pending']
    assert not (dest / 'b.sfc').exists()


def test_delete_batch_full_folder_registers_directory_path(tmp_path):
    folder = tmp_path / 'Game'
    folder.mkdir()
    (folder / 'a.bin').write_bytes(b'a')
    solo = tmp_path / 'x.sfc'
    solo.write_bytes(b'x')
    result = ops.apply_delete_batch([str(folder), str(solo)], False)
    assert not result['cancelled']
    assert [item['status'] for item in result['items']] == ['deleted', 'deleted']
    assert result['items'][0]['folder'] is True and result['items'][1]['folder'] is False
    roms = tmp_path / 'Roms'
    roms.mkdir()
    root, target = library(tmp_path, [
        {'path': str(folder / 'a.bin'), 'label': 'A'},
        {'path': str(solo), 'label': 'X'},
        {'path': str(roms / 'keep.sfc'), 'label': 'K'},
    ])
    public = sync.preview(str(root), _sync_changes(result))['public']
    assert sorted(entry['label'] for entry in public['removed']) == ['A', 'X']


def test_delete_batch_partial_folder_registers_only_deleted_files(tmp_path, monkeypatch):
    folder = tmp_path / 'Game'
    folder.mkdir()
    for name in ('a.bin', 'b.bin'):
        (folder / name).write_bytes(name.encode())
    later = tmp_path / 'later.sfc'
    later.write_bytes(b'later')
    real_unlink = Path.unlink
    calls = [0]

    def flaky(path, *args, **kwargs):
        calls[0] += 1
        if calls[0] > 1:
            raise OSError('设备 I/O 错误')
        return real_unlink(path, *args, **kwargs)

    monkeypatch.setattr(Path, 'unlink', flaky)
    result = ops.apply_delete_batch([str(folder), str(later)], False)
    assert not result['cancelled']
    first, second = result['items']
    assert first['status'] == 'partial' and first['folder'] is True
    assert len(first['deleted']) == 1
    assert second['status'] == 'pending' and later.exists()
    root, target = library(tmp_path, [
        {'path': str(folder / 'a.bin'), 'label': 'A'},
        {'path': str(folder / 'b.bin'), 'label': 'B'},
        {'path': str(later), 'label': 'L'},
    ])
    public = sync.preview(str(root), _sync_changes(result))['public']
    removed = {entry['label'] for entry in public['removed']}
    assert len(removed) == 1 and 'L' not in removed


def test_delete_batch_cancel_marks_partial_and_pending(tmp_path):
    from task_control import TaskCancelled
    folder = tmp_path / 'Game'
    folder.mkdir()
    for name in ('a.bin', 'b.bin', 'c.bin'):
        (folder / name).write_bytes(name.encode())
    solo = tmp_path / 'solo.sfc'
    solo.write_bytes(b'x')
    calls = [0]

    def cancelled():
        calls[0] += 1
        if calls[0] > 2:
            raise TaskCancelled('stop')

    result = ops.apply_delete_batch([str(folder), str(solo)], False, cancelled=cancelled)
    assert result['cancelled']
    assert result['items'][0]['status'] == 'partial' and result['items'][0]['deleted']
    assert result['items'][1]['status'] == 'pending' and solo.exists()


def test_import_preview_reports_conflict_metadata_stamps_and_duplicates(tmp_path):
    dest = tmp_path / 'dest'
    dest.mkdir()
    (dest / 'game.sfc').write_bytes(b'old')
    (dest / 'Game').mkdir()
    src = tmp_path / 'src'
    src.mkdir()
    (src / 'game.sfc').write_bytes(b'new')
    (src / 'Game').mkdir()
    (src / 'Game' / 'x.bin').write_bytes(b'x')
    (src / 'free.sfc').write_bytes(b'f')
    dup_a = tmp_path / 'a' / 'same.sfc'
    dup_b = tmp_path / 'b' / 'same.sfc'
    dup_a.parent.mkdir()
    dup_b.parent.mkdir()
    dup_a.write_bytes(b'1')
    dup_b.write_bytes(b'2')
    preview = ops.preview_import([str(src / 'game.sfc'), str(src / 'Game'), str(src / 'free.sfc'),
                                  str(dup_a), str(dup_b)], str(dest), False)
    items = preview['items']
    conflict = items[0]['conflict']
    assert conflict['folder'] is False and conflict['size'] == 3 and conflict['stamp']
    folder_conflict = items[1]['conflict']
    assert folder_conflict['folder'] is True and folder_conflict['size'] is None and folder_conflict['stamp']
    assert items[2]['conflict'] is None and items[2]['size'] == 1
    assert preview['duplicates'] == [{'name': 'same.sfc', 'sources': [3, 4]}]
    assert items[3]['duplicate'] and items[4]['duplicate']


def test_add_decisions_skip_replace_and_keep_unapproved_targets(tmp_path, monkeypatch):
    monkeypatch.setenv('RETROARCH_ASSISTANT_HOME', str(tmp_path / 'home'))
    dest = tmp_path / 'dest'
    dest.mkdir()
    for name, content in (('skip.sfc', b'orig'), ('swap.sfc', b'orig'), ('changed.sfc', b'v1')):
        (dest / name).write_bytes(content)
    src = tmp_path / 'src'
    src.mkdir()
    for name in ('skip.sfc', 'swap.sfc', 'changed.sfc', 'appeared.sfc'):
        (src / name).write_bytes(b'new ' + name.encode())
    stale_stamp = ops.target_stamp(str(dest / 'changed.sfc'), False)
    swap_stamp = ops.target_stamp(str(dest / 'swap.sfc'), False)
    (dest / 'changed.sfc').write_bytes(b'v2')
    (dest / 'appeared.sfc').write_bytes(b'surprise')
    decisions = {
        str(src / 'skip.sfc'): {'action': 'skip'},
        str(src / 'swap.sfc'): {'action': 'replace', 'stamp': swap_stamp},
        str(src / 'appeared.sfc'): {'action': 'copy'},
        str(src / 'changed.sfc'): {'action': 'replace', 'stamp': stale_stamp},
    }
    result = ops.apply_add([str(src / name) for name in ('skip.sfc', 'swap.sfc', 'changed.sfc', 'appeared.sfc')],
                           str(dest), False, decisions=decisions)
    assert result['skipped'] == ['skip.sfc'] and result['replaced'] == ['swap.sfc']
    assert {entry['name']: entry['reason'] for entry in result['kept']} == {
        'changed.sfc': 'changed', 'appeared.sfc': 'appeared'}
    assert (dest / 'swap.sfc').read_bytes() == b'new swap.sfc'
    assert (dest / 'changed.sfc').read_bytes() == b'v2'
    assert (dest / 'appeared.sfc').read_bytes() == b'surprise'
    # The reviewed original stays parked; only staged residue is forbidden.
    residue = [p for p in dest.glob('*.plcn-*') if '.plcn-old-' not in p.name]
    assert not residue


def test_add_dup_sources_copy_only_the_chosen_one(tmp_path):
    dest = tmp_path / 'dest'
    dest.mkdir()
    dir_a, dir_b = tmp_path / 'a', tmp_path / 'b'
    dir_a.mkdir(); dir_b.mkdir()
    (dir_a / 'same.sfc').write_bytes(b'A')
    (dir_b / 'same.sfc').write_bytes(b'B')
    decisions = {
        str(dir_a / 'same.sfc'): {'action': 'copy'},
        str(dir_b / 'same.sfc'): {'action': 'skip'},
    }
    result = ops.apply_add([str(dir_a / 'same.sfc'), str(dir_b / 'same.sfc')],
                           str(dest), False, decisions=decisions)
    assert result['copied'] == ['same.sfc'] and result['skipped'] == ['same.sfc']
    assert (dest / 'same.sfc').read_bytes() == b'A'


def test_validate_decisions_rejects_unknown_sources_and_bad_actions(tmp_path):
    src = tmp_path / 'src'
    src.mkdir()
    (src / 'a.sfc').write_bytes(b'a')
    dest = tmp_path / 'dest'
    dest.mkdir()
    sources, directory, remote, overwrite = ops.validate_add({
        'action': 'add', 'sources': [str(src / 'a.sfc')], 'directory': str(dest), 'root': str(tmp_path)})
    with pytest.raises(ValueError):
        ops.validate_decisions({'decisions': {str(tmp_path / 'other.sfc'): {'action': 'copy'}}}, sources)
    with pytest.raises(ValueError):
        ops.validate_decisions({'decisions': {sources[0]: {'action': 'delete'}}}, sources)
    with pytest.raises(ValueError):
        ops.validate_decisions({'decisions': 'yes'}, sources)
    assert ops.validate_decisions({}, sources) is None
    assert ops.validate_decisions({'decisions': {sources[0]: {'action': 'copy'}}}, sources) == {
        sources[0]: {'action': 'copy', 'stamp': None}}


def test_target_stamp_tracks_file_and_folder_changes(tmp_path):
    file = tmp_path / 'game.sfc'
    file.write_bytes(b'1')
    first = ops.target_stamp(str(file), False)
    file.write_bytes(b'22')
    assert ops.target_stamp(str(file), False) != first
    folder = tmp_path / 'Game'
    folder.mkdir()
    before = ops.target_stamp(str(folder), False)
    (folder / 'a.bin').write_bytes(b'a')
    assert ops.target_stamp(str(folder), False) != before
    assert ops.target_stamp(str(tmp_path / 'missing'), False) is None


def test_add_copy_keeps_a_file_target_appearing_during_write(tmp_path, monkeypatch):
    """GF-04-R1: a same-named file created after the existence check stays untouched."""
    dest = tmp_path / 'dest'
    dest.mkdir()
    src = tmp_path / 'src'
    src.mkdir()
    (src / 'free.sfc').write_bytes(b'new')
    real_write = ops._write_item

    def hook(item, target, remote, cancelled, on_file):
        (dest / 'free.sfc').write_bytes(b'outside')
        return real_write(item, target, remote, cancelled, on_file)

    monkeypatch.setattr(ops, '_write_item', hook)
    source = str(src / 'free.sfc')
    result = ops.apply_add([source], str(dest), False, decisions={source: {'action': 'copy'}})
    assert (dest / 'free.sfc').read_bytes() == b'outside'
    assert result['copied'] == [] and result['replaced'] == [] and result['failed'] == []
    assert result['kept'] == [{'name': 'free.sfc', 'reason': 'appeared'}]
    assert not list(dest.glob('*.plcn-*'))


def test_add_copy_keeps_a_dir_target_appearing_during_write(tmp_path, monkeypatch):
    """GF-04-R1: a same-named folder appearing mid-run is kept, with the item located."""
    dest = tmp_path / 'dest'
    dest.mkdir()
    src = tmp_path / 'src'
    src.mkdir()
    (src / 'Pack').mkdir()
    (src / 'Pack' / 'new.bin').write_bytes(b'new')
    real_write = ops._write_item

    def hook(item, target, remote, cancelled, on_file):
        (dest / 'Pack').mkdir()
        (dest / 'Pack' / 'inside.bin').write_bytes(b'outside')
        return real_write(item, target, remote, cancelled, on_file)

    monkeypatch.setattr(ops, '_write_item', hook)
    source = str(src / 'Pack')
    result = ops.apply_add([source], str(dest), False, decisions={source: {'action': 'copy'}})
    assert (dest / 'Pack' / 'inside.bin').read_bytes() == b'outside'
    assert not (dest / 'Pack' / 'new.bin').exists()
    assert result['kept'] == [{'name': 'Pack', 'reason': 'appeared'}]
    assert not list(dest.glob('*.plcn-*'))


def test_add_copy_without_decisions_still_keeps_a_late_target(tmp_path, monkeypatch):
    """GF-04-R1: the no-preview flow reports a late same-name target as skipped."""
    dest = tmp_path / 'dest'
    dest.mkdir()
    src = tmp_path / 'src'
    src.mkdir()
    (src / 'free.sfc').write_bytes(b'new')
    real_write = ops._write_item

    def hook(item, target, remote, cancelled, on_file):
        (dest / 'free.sfc').write_bytes(b'outside')
        return real_write(item, target, remote, cancelled, on_file)

    monkeypatch.setattr(ops, '_write_item', hook)
    result = ops.apply_add([str(src / 'free.sfc')], str(dest), False)
    assert (dest / 'free.sfc').read_bytes() == b'outside'
    assert result['skipped'] == ['free.sfc'] and result['copied'] == []
    assert not list(dest.glob('*.plcn-*'))


def test_add_replace_detects_member_content_change(tmp_path):
    """GF-04-R2: editing an existing member file invalidates the approved replace."""
    dest = tmp_path / 'dest'
    (dest / 'Game' / 'nested').mkdir(parents=True)
    (dest / 'Game' / 'keep.bin').write_bytes(b'v1')
    (dest / 'Game' / 'nested' / 'deep.bin').write_bytes(b'v1')
    src = tmp_path / 'src'
    (src / 'Game').mkdir(parents=True)
    (src / 'Game' / 'a.bin').write_bytes(b'new')
    source = str(src / 'Game')
    stamp = ops.preview_import([source], str(dest), False)['items'][0]['conflict']['stamp']
    (dest / 'Game' / 'keep.bin').write_bytes(b'v2-edited-longer')
    decisions = {source: {'action': 'replace', 'stamp': stamp}}
    result = ops.apply_add([source], str(dest), False, decisions=decisions)
    assert result['kept'] == [{'name': 'Game', 'reason': 'changed'}]
    assert result['replaced'] == [] and result['failed'] == []
    assert (dest / 'Game' / 'keep.bin').read_bytes() == b'v2-edited-longer'
    assert not list(dest.glob('*.plcn-*'))
    # A nested-file edit is caught the same way.
    stamp = ops.preview_import([source], str(dest), False)['items'][0]['conflict']['stamp']
    (dest / 'Game' / 'nested' / 'deep.bin').write_bytes(b'v2-edited')
    decisions = {source: {'action': 'replace', 'stamp': stamp}}
    result = ops.apply_add([source], str(dest), False, decisions=decisions)
    assert result['kept'] == [{'name': 'Game', 'reason': 'changed'}]
    assert (dest / 'Game' / 'nested' / 'deep.bin').read_bytes() == b'v2-edited'
    # An unchanged target still honors the original approval.
    stamp = ops.preview_import([source], str(dest), False)['items'][0]['conflict']['stamp']
    decisions = {source: {'action': 'replace', 'stamp': stamp}}
    result = ops.apply_add([source], str(dest), False, decisions=decisions)
    assert result['replaced'] == ['Game']
    assert not (dest / 'Game' / 'nested').exists()
    assert (dest / 'Game' / 'a.bin').read_bytes() == b'new'


def test_add_replace_without_stamp_is_kept_unverifiable(tmp_path):
    """GF-04-R2: an approved replace with no verifiable fingerprint keeps the target."""
    dest = tmp_path / 'dest'
    (dest / 'Game').mkdir(parents=True)
    (dest / 'Game' / 'keep.bin').write_bytes(b'v1')
    src = tmp_path / 'src'
    (src / 'Game').mkdir(parents=True)
    (src / 'Game' / 'a.bin').write_bytes(b'new')
    source = str(src / 'Game')
    decisions = {source: {'action': 'replace'}}
    result = ops.apply_add([source], str(dest), False, decisions=decisions)
    assert result['kept'] == [{'name': 'Game', 'reason': 'unverifiable'}]
    assert (dest / 'Game' / 'keep.bin').read_bytes() == b'v1'
    assert not list(dest.glob('*.plcn-*'))


def _mtp_session_stub(monkeypatch):
    import contextlib

    @contextlib.contextmanager
    def fake_session(value):
        yield (None, None, list(ops.mtp.parse(value)[2]))
    monkeypatch.setattr(ops.mtp, 'session', fake_session)
    monkeypatch.setattr(ops.mtp, 'resolve', lambda handle, storage, parts, create=False:
                        {'id': 10, 'folder': True})


def test_delete_batch_survives_disconnect_without_querying_pending(tmp_path, monkeypatch):
    """GF-03-R1: confirmed results are returned even when a later stat fails."""
    import mtp_runtime as mtp
    uri1, uri2, uri3 = [mtp.uri('dev', 0x10001, name) for name in ('a.sfc', 'b.sfc', 'c.sfc')]
    queried = []

    def stat(path):
        queried.append(path)
        if path == uri2:
            raise RuntimeError('USB 断开')
        if path == uri1:
            return {'id': 1, 'folder': False, 'size': 1}
        return None

    monkeypatch.setattr(mtp, 'stat_file', stat)
    deleted = []
    monkeypatch.setattr(mtp, 'delete_object', lambda path: deleted.append(path))
    _mtp_session_stub(monkeypatch)
    result = ops.apply_delete_batch([uri1, uri2, uri3], True)
    assert [item['status'] for item in result['items']] == ['deleted', 'failed', 'pending']
    assert 'USB' in result['items'][1]['error']
    assert deleted == [uri1]
    assert uri3 not in queried


def _mtp_copy_stubs(tmp_path, monkeypatch, fail_upload):
    import mtp_runtime as mtp
    pack = mtp.uri('dev', 0x10001, 'Roms/Pack')
    dest = mtp.uri('dev', 0x10001, 'Target')
    later = mtp.uri('dev', 0x10001, 'Roms/later.sfc')
    children = [
        {'id': 1, 'name': 'a.bin', 'size': 1, 'folder': False, 'modified': None},
        {'id': 2, 'name': 'b.bin', 'size': 1, 'folder': False, 'modified': None},
    ]

    def stat(path):
        if path == pack:
            return {'id': 9, 'folder': True, 'size': 0}
        return None

    monkeypatch.setattr(mtp, 'stat_file', stat)
    monkeypatch.setattr(mtp, 'listing', lambda path: children if path == pack else [])
    monkeypatch.setattr(mtp, 'delete_object', lambda path: None)
    _mtp_session_stub(monkeypatch)
    monkeypatch.setattr(ops.chd_io, 'copy_to',
                        lambda source, target, cancelled=lambda: None: Path(target).write_bytes(b'd'))
    monkeypatch.setattr(ops.chd_io, 'digest', lambda *args: 'digest')
    monkeypatch.setattr(mtp, 'upload_file', fail_upload)
    return pack, dest, later


def test_copy_batch_reports_partial_remote_folder(tmp_path, monkeypatch):
    """GF-03-R2: a mid-folder disconnect reports the real target and written files."""
    from task_control import TaskCancelled

    def upload(target, source, expected, stage, cancelled=lambda: None):
        if 'b.bin' in target:
            raise RuntimeError('传输中断')

    pack, dest, later = _mtp_copy_stubs(tmp_path, monkeypatch, upload)
    result = ops.apply_copy_batch([pack, later], dest, True)
    first, second = result['items']
    assert first['status'] == 'partial'
    assert first['target'] == ops.mtp.join(dest, 'Pack')
    assert first['written'] == [ops.mtp.join(dest, 'Pack', 'a.bin')]
    assert second['status'] == 'pending' and not result['cancelled']


def test_copy_batch_partial_marks_cancelled(tmp_path, monkeypatch):
    """GF-03-R2: cancellation mid-copy is distinguished from a transport failure."""
    from task_control import TaskCancelled

    def upload(target, source, expected, stage, cancelled=lambda: None):
        if 'b.bin' in target:
            raise TaskCancelled('stop')

    pack, dest, later = _mtp_copy_stubs(tmp_path, monkeypatch, upload)
    result = ops.apply_copy_batch([pack, later], dest, True)
    assert result['cancelled']
    assert result['items'][0]['status'] == 'partial'
    assert result['items'][0]['written'] == [ops.mtp.join(dest, 'Pack', 'a.bin')]
    assert result['items'][1]['status'] == 'pending'


def test_drop_sources_have_no_time_based_pairing():
    """C-01/C-02: source batches only bind by the drop's own nonce; there is
    no time-window pairing left for a foreign record to borrow, and the Cocoa
    capture delivers asynchronously so the main thread never waits on JS."""
    import inspect
    import desktop_window
    assert not hasattr(desktop_window, '_find_drop_paths')
    assert not hasattr(desktop_window, '_match_drop_files')
    source = inspect.getsource(desktop_window._capture_drop_urls)
    assert 'evaluateJavaScript_completionHandler_' in source
    assert 'window.evaluate_js(' not in source and 'window.run_js(' not in source



def test_publish_hook_rejects_a_file_appearing_at_rename(tmp_path, monkeypatch):
    """GF-04-R1(2): the exclusive rename itself refuses a just-appeared file."""
    dest = tmp_path / 'dest'
    dest.mkdir()
    src = tmp_path / 'src'
    src.mkdir()
    (src / 'free.sfc').write_bytes(b'new')
    real_rename = ops._rename_exclusive

    def hook(staged, target):
        Path(target).write_bytes(b'outside')
        return real_rename(staged, target)

    monkeypatch.setattr(ops, '_rename_exclusive', hook)
    source = str(src / 'free.sfc')
    result = ops.apply_add([source], str(dest), False, decisions={source: {'action': 'copy'}})
    assert (dest / 'free.sfc').read_bytes() == b'outside'
    assert result['copied'] == [] and result['replaced'] == []
    assert result['kept'] == [{'name': 'free.sfc', 'reason': 'appeared'}]
    assert not list(dest.glob('*.plcn-*'))


def test_publish_hook_rejects_an_empty_dir_appearing_at_rename(tmp_path, monkeypatch):
    """GF-04-R1(2): a just-appeared empty directory is preserved too."""
    dest = tmp_path / 'dest'
    dest.mkdir()
    src = tmp_path / 'src'
    src.mkdir()
    (src / 'Pack').mkdir()
    (src / 'Pack' / 'new.bin').write_bytes(b'new')
    real_rename = ops._rename_exclusive

    def hook(staged, target):
        Path(target).mkdir()
        return real_rename(staged, target)

    monkeypatch.setattr(ops, '_rename_exclusive', hook)
    source = str(src / 'Pack')
    result = ops.apply_add([source], str(dest), False, decisions={source: {'action': 'copy'}})
    assert (dest / 'Pack').is_dir() and not list((dest / 'Pack').iterdir())
    assert result['kept'] == [{'name': 'Pack', 'reason': 'appeared'}]
    assert not list(dest.glob('*.plcn-*'))


def test_mtp_publish_keeps_target_created_at_rename(tmp_path, monkeypatch):
    """GF-04-R1(2): a device refusing the rename over an appeared object keeps it."""
    import mtp_runtime as mtp
    root_uri = mtp.uri('dev', 0x10001, 'Roms')
    files = {}

    def rename(path, name):
        target = path.rsplit('/', 1)[0] + '/' + name
        if target in files:
            raise RuntimeError('device refused')
        files[target] = files.pop(path)
        return target

    monkeypatch.setattr(mtp, 'stat_file',
                        lambda path: {'id': 1, 'folder': False, 'size': 1} if path in files else None)
    monkeypatch.setattr(mtp, 'listing', lambda path: [])
    monkeypatch.setattr(mtp, 'upload_file', lambda target, source, expected, stage, cancelled=lambda: None:
                        files.__setitem__(target, Path(source).read_bytes()))
    monkeypatch.setattr(mtp, 'rename_object', rename)
    monkeypatch.setattr(mtp, 'delete_object', lambda path: files.pop(path, None))
    _mtp_session_stub(monkeypatch)

    source = tmp_path / 'game.sfc'
    source.write_bytes(b'new version')
    target_uri = mtp.join(root_uri, 'game.sfc')
    decisions = {str(source): {'action': 'copy'}}
    # The outside object materializes only once the staged copy exists.
    real_publish = ops._publish_new

    def hook(staged, target, remote):
        files[target_uri] = b'outside'
        return real_publish(staged, target, remote)

    monkeypatch.setattr(ops, '_publish_new', hook)
    result = ops.apply_add([str(source)], root_uri, True, decisions=decisions)
    assert files[target_uri] == b'outside'
    assert result['kept'] == [{'name': 'game.sfc', 'reason': 'appeared'}]
    assert not any('.plcn-' in path for path in files)


def _mtp_tree_stubs(monkeypatch, members):
    """MTP stubs whose Target folder returns `members` from listing()."""
    import mtp_runtime as mtp
    root_uri = mtp.uri('dev', 0x10001, 'Roms')
    target_uri = mtp.join(root_uri, 'Game')
    store = {target_uri: {'id': 9, 'folder': True, 'size': 0}}
    member_state = dict(members)

    def stat(path):
        if path in store:
            return store[path]
        for name, meta in member_state.items():
            if path == mtp.join(target_uri, name):
                return {'id': meta['id'], 'folder': False,
                        'size': meta['size'], 'modified': meta.get('modified')}
        return None

    def listing(path):
        if path == target_uri:
            return [{'id': meta['id'], 'name': name, 'size': meta['size'],
                     'folder': False, 'modified': meta.get('modified')}
                    for name, meta in member_state.items()]
        return []

    def rename(path, name):
        new_path = path.rsplit('/', 1)[0] + '/' + name
        if path in store:
            store[new_path] = store.pop(path)
        for member in list(member_state):
            if path == mtp.join(target_uri, member):
                member_state[name] = member_state.pop(member)
        return new_path

    monkeypatch.setattr(mtp, 'stat_file', stat)
    monkeypatch.setattr(mtp, 'listing', listing)
    monkeypatch.setattr(mtp, 'upload_file', lambda target, source, expected, stage, cancelled=lambda: None: None)
    monkeypatch.setattr(mtp, 'rename_object', rename)
    monkeypatch.setattr(mtp, 'delete_object', lambda path: None)
    _mtp_session_stub(monkeypatch)
    return root_uri, target_uri, member_state


def test_mtp_stamp_detects_member_replaced_with_new_id(tmp_path, monkeypatch):
    """GF-04-R2(2): a same-size member swapped for a new object id keeps the target."""
    members = {'keep.bin': {'id': 5, 'size': 4, 'modified': 100}}
    root_uri, target_uri, member_state = _mtp_tree_stubs(monkeypatch, members)
    src = tmp_path / 'Game'
    src.mkdir()
    (src / 'a.bin').write_bytes(b'new')
    source = str(src)
    stamp = ops.preview_import([source], root_uri, True)['items'][0]['conflict']['stamp']
    # Same name and size, different contents -> different object id.
    member_state['keep.bin'] = {'id': 6, 'size': 4, 'modified': 100}
    result = ops.apply_add([source], root_uri, True, decisions={source: {'action': 'replace', 'stamp': stamp}})
    assert result['kept'] == [{'name': 'Game', 'reason': 'changed'}]
    assert result['replaced'] == []


def test_mtp_stamp_unverifiable_when_member_lacks_mtime(tmp_path, monkeypatch):
    """GF-04-R2(2): no timestamp means no reliable evidence; the target is kept."""
    members = {'keep.bin': {'id': 5, 'size': 4, 'modified': None}}
    root_uri, target_uri, member_state = _mtp_tree_stubs(monkeypatch, members)
    src = tmp_path / 'Game'
    src.mkdir()
    (src / 'a.bin').write_bytes(b'new')
    source = str(src)
    stamp = ops.preview_import([source], root_uri, True)['items'][0]['conflict']['stamp']
    # Content may have changed in place: same id, no mtime to prove otherwise.
    result = ops.apply_add([source], root_uri, True, decisions={source: {'action': 'replace', 'stamp': stamp}})
    assert result['kept'] == [{'name': 'Game', 'reason': 'unverifiable'}]
    assert result['replaced'] == []


def test_mtp_verified_folder_replace_still_applies(tmp_path, monkeypatch):
    """GF-04-R2(2): a fully timestamped tree with unchanged ids may be replaced."""
    members = {'keep.bin': {'id': 5, 'size': 4, 'modified': 100},
               'other.bin': {'id': 7, 'size': 8, 'modified': 200}}
    root_uri, target_uri, member_state = _mtp_tree_stubs(monkeypatch, members)
    src = tmp_path / 'Game'
    src.mkdir()
    (src / 'a.bin').write_bytes(b'new')
    source = str(src)
    stamp = ops.preview_import([source], root_uri, True)['items'][0]['conflict']['stamp']
    result = ops.apply_add([source], root_uri, True, decisions={source: {'action': 'replace', 'stamp': stamp}})
    assert result['replaced'] == ['Game']
    assert result['kept'] == []


def test_copy_single_unwraps_cancelled_copy_partial(tmp_path, monkeypatch):
    """GF-03-R3: single-path copy cancellation reports TaskCancelled, not failure."""
    import mtp_runtime as mtp
    from task_control import TaskCancelled
    source_uri = mtp.uri('dev', 0x10001, 'Roms/Pack')
    dest_uri = mtp.uri('dev', 0x10001, 'Target')
    children = [{'id': 1, 'name': 'a.bin', 'size': 1, 'folder': False, 'modified': None},
                {'id': 2, 'name': 'b.bin', 'size': 1, 'folder': False, 'modified': None}]
    monkeypatch.setattr(mtp, 'stat_file',
                        lambda path: {'id': 9, 'folder': True, 'size': 0} if path == source_uri else None)
    monkeypatch.setattr(mtp, 'listing', lambda path: children if path == source_uri else [])
    monkeypatch.setattr(mtp, 'delete_object', lambda path: None)
    _mtp_session_stub(monkeypatch)

    def upload(target, source, expected, stage, cancelled=lambda: None):
        if 'b.bin' in target:
            raise TaskCancelled('stop')

    monkeypatch.setattr(mtp, 'upload_file', upload)
    monkeypatch.setattr(ops.chd_io, 'copy_to',
                        lambda source, target, cancelled=lambda: None: Path(target).write_bytes(b'd'))
    monkeypatch.setattr(ops.chd_io, 'digest', lambda *args: 'digest')
    with pytest.raises(TaskCancelled):
        ops.apply_copy_single(source_uri, dest_uri, True)


def test_copy_single_unwraps_failed_copy_partial(tmp_path, monkeypatch):
    """GF-03-R3: a real single-path failure stays a failure."""
    import mtp_runtime as mtp
    from task_control import TaskCancelled
    source_uri = mtp.uri('dev', 0x10001, 'Roms/Pack')
    dest_uri = mtp.uri('dev', 0x10001, 'Target')
    children = [{'id': 1, 'name': 'a.bin', 'size': 1, 'folder': False, 'modified': None},
                {'id': 2, 'name': 'b.bin', 'size': 1, 'folder': False, 'modified': None}]
    monkeypatch.setattr(mtp, 'stat_file',
                        lambda path: {'id': 9, 'folder': True, 'size': 0} if path == source_uri else None)
    monkeypatch.setattr(mtp, 'listing', lambda path: children if path == source_uri else [])
    monkeypatch.setattr(mtp, 'delete_object', lambda path: None)
    _mtp_session_stub(monkeypatch)

    def upload(target, source, expected, stage, cancelled=lambda: None):
        if 'b.bin' in target:
            raise RuntimeError('传输中断')

    monkeypatch.setattr(mtp, 'upload_file', upload)
    monkeypatch.setattr(ops.chd_io, 'copy_to',
                        lambda source, target, cancelled=lambda: None: Path(target).write_bytes(b'd'))
    monkeypatch.setattr(ops.chd_io, 'digest', lambda *args: 'digest')
    with pytest.raises(RuntimeError):
        ops.apply_copy_single(source_uri, dest_uri, True)
    with pytest.raises(ops.CopyPartial):
        ops.apply_copy(source_uri, dest_uri, True)


def _no_renamex(monkeypatch):
    """Force the portable claim path as on ExFAT (renamex_np unsupported)."""
    import ctypes
    import errno
    if hasattr(ops, '_renamex_np'):
        def fake(*args):
            ctypes.set_errno(errno.ENOTSUP)
            return -1
        monkeypatch.setattr(ops, '_renamex_np', fake)


def test_publish_fd_write_never_touches_a_swapped_placeholder(tmp_path, monkeypatch):
    """GF-04-R1(3): the claimed fd writes to our inode; a swapped file is kept."""
    dest = tmp_path / 'dest'
    dest.mkdir()
    src = tmp_path / 'src'
    src.mkdir()
    (src / 'free.sfc').write_bytes(b'new')
    _no_renamex(monkeypatch)
    monkeypatch.setattr(ops.os, 'link',
                        lambda *args: (_ for _ in ()).throw(OSError('no links on this volume')))
    target = dest / 'free.sfc'
    real_copyfileobj = shutil.copyfileobj

    def hook(source, sink, *args):
        # The name we claimed now belongs to a foreign file with other bytes.
        target.unlink()
        target.write_bytes(b'outside')
        return real_copyfileobj(source, sink, *args)

    monkeypatch.setattr(ops.shutil, 'copyfileobj', hook)
    source = str(src / 'free.sfc')
    result = ops.apply_add([source], str(dest), False, decisions={source: {'action': 'copy'}})
    assert target.read_bytes() == b'outside'
    assert result['copied'] == [] and result['replaced'] == []
    assert result['kept'] == [{'name': 'free.sfc', 'reason': 'appeared'}]
    assert not list(dest.glob('*.plcn-*'))


def test_publish_dir_keeps_a_foreign_child_created_after_claim(tmp_path, monkeypatch):
    """GF-04-R1(3): a same-named member appearing inside the claimed dir is kept."""
    dest = tmp_path / 'dest'
    dest.mkdir()
    src = tmp_path / 'src'
    (src / 'Pack').mkdir(parents=True)
    (src / 'Pack' / 'new.bin').write_bytes(b'new')
    _no_renamex(monkeypatch)
    real_mkdir = os.mkdir
    injected = []

    def fake_mkdir(path, mode=0o777):
        real_mkdir(path, mode)
        if not injected and Path(path).name == 'Pack':
            injected.append(path)
            (Path(path) / 'new.bin').write_bytes(b'outside')

    monkeypatch.setattr(os, 'mkdir', fake_mkdir)
    source = str(src / 'Pack')
    result = ops.apply_add([source], str(dest), False, decisions={source: {'action': 'copy'}})
    assert (dest / 'Pack' / 'new.bin').read_bytes() == b'outside'
    assert result['copied'] == [] and result['kept'] == []
    assert result['failed'] and 'new.bin' in result['failed'][0]['error']
    # Cleanup only removes the staged remainder; the claimed dir keeps its
    # real contents (the foreign member) instead of being swept wholesale.
    assert not list(dest.glob('*.plcn-*'))


def test_add_replace_detects_same_size_same_mtime_rewrite(tmp_path):
    """GF-04-R3: an in-place rewrite invisible to size+mtime still fails the check."""
    dest = tmp_path / 'dest'
    (dest / 'Game').mkdir(parents=True)
    keep = dest / 'Game' / 'keep.bin'
    keep.write_bytes(b'aaaa')
    src = tmp_path / 'src'
    (src / 'Game').mkdir(parents=True)
    (src / 'Game' / 'a.bin').write_bytes(b'new')
    source = str(src / 'Game')
    stamp = ops.preview_import([source], str(dest), False)['items'][0]['conflict']['stamp']
    mtime = keep.stat().st_mtime_ns
    keep.write_bytes(b'bbbb')  # same length, different content
    os.utime(keep, ns=(keep.stat().st_atime_ns, mtime))  # coarse ExFAT mtime survives
    decisions = {source: {'action': 'replace', 'stamp': stamp}}
    result = ops.apply_add([source], str(dest), False, decisions=decisions)
    assert result['kept'] == [{'name': 'Game', 'reason': 'changed'}]
    assert result['replaced'] == [] and result['failed'] == []
    assert keep.read_bytes() == b'bbbb'
    assert not list(dest.glob('*.plcn-*'))
    # The unchanged target still honors the same approval flow.
    stamp = ops.preview_import([source], str(dest), False)['items'][0]['conflict']['stamp']
    result = ops.apply_add([source], str(dest), False, decisions={source: {'action': 'replace', 'stamp': stamp}})
    assert result['replaced'] == ['Game']
    assert (dest / 'Game' / 'a.bin').read_bytes() == b'new'


def _mtp_nested_replace_stubs(monkeypatch, bin_id=11, bin_modified=100):
    """MTP stubs for Roms/Game/Disc/game.bin; returns mutable stat/listing maps."""
    import mtp_runtime as mtp
    root_uri = mtp.uri('dev', 0x10001, 'Roms')
    target_uri = mtp.join(root_uri, 'Game')
    disc_uri = mtp.join(target_uri, 'Disc')
    bin_uri = mtp.join(disc_uri, 'game.bin')
    stat_map = {
        target_uri: {'id': 9, 'folder': True, 'size': 0},
        disc_uri: {'id': 10, 'folder': True, 'size': 0},
        bin_uri: {'id': bin_id, 'folder': False, 'size': 4, 'modified': bin_modified},
    }
    listing_map = {
        target_uri: [{'id': 10, 'name': 'Disc', 'size': 0, 'folder': True, 'modified': None}],
        disc_uri: [{'id': bin_id, 'name': 'game.bin', 'size': 4, 'folder': False,
                    'modified': bin_modified}],
    }
    def rename(path, name):
        new_path = path.rsplit('/', 1)[0] + '/' + name
        for uri in list(stat_map):
            if uri == path or uri.startswith(path + '/'):
                stat_map[new_path + uri[len(path):]] = stat_map.pop(uri)
        for uri in list(listing_map):
            if uri == path or uri.startswith(path + '/'):
                listing_map[new_path + uri[len(path):]] = listing_map.pop(uri)
        return new_path

    monkeypatch.setattr(mtp, 'stat_file', lambda path: stat_map.get(path))
    monkeypatch.setattr(mtp, 'listing', lambda path: listing_map.get(path, []))
    monkeypatch.setattr(mtp, 'upload_file', lambda *args, **kwargs: None)
    monkeypatch.setattr(mtp, 'rename_object', rename)
    monkeypatch.setattr(mtp, 'delete_object', lambda path: None)
    _mtp_session_stub(monkeypatch)
    return root_uri, stat_map, listing_map


def test_mtp_replace_allows_a_fully_verified_nested_tree(tmp_path, monkeypatch):
    """GF-04-R2(3): nested dirs with complete evidence may be replaced."""
    root_uri, stat_map, listing_map = _mtp_nested_replace_stubs(monkeypatch)
    src = tmp_path / 'Game'
    src.mkdir()
    (src / 'a.bin').write_bytes(b'new')
    source = str(src)
    stamp = ops.preview_import([source], root_uri, True)['items'][0]['conflict']['stamp']
    assert '"verifiable": true' in stamp
    result = ops.apply_add([source], root_uri, True, decisions={source: {'action': 'replace', 'stamp': stamp}})
    assert result['replaced'] == ['Game']
    assert result['kept'] == []


def test_mtp_nested_member_swapped_for_a_new_id_is_kept(tmp_path, monkeypatch):
    """GF-04-R2(3): a nested file replaced by a new object id stays untouched."""
    root_uri, stat_map, listing_map = _mtp_nested_replace_stubs(monkeypatch)
    src = tmp_path / 'Game'
    src.mkdir()
    (src / 'a.bin').write_bytes(b'new')
    source = str(src)
    stamp = ops.preview_import([source], root_uri, True)['items'][0]['conflict']['stamp']
    # Same name and size at a nested path, but a different object id.
    import mtp_runtime as mtp
    bin_uri = mtp.join(mtp.join(root_uri, 'Game', 'Disc'), 'game.bin')
    stat_map[bin_uri] = {'id': 12, 'folder': False, 'size': 4, 'modified': 100}
    listing_map[mtp.join(root_uri, 'Game', 'Disc')][0]['id'] = 12
    result = ops.apply_add([source], root_uri, True, decisions={source: {'action': 'replace', 'stamp': stamp}})
    assert result['kept'] == [{'name': 'Game', 'reason': 'changed'}]
    assert result['replaced'] == []


def test_mtp_nested_member_without_mtime_is_unverifiable(tmp_path, monkeypatch):
    """GF-04-R2(3): a nested file lacking mtime gives no reliable evidence."""
    root_uri, stat_map, listing_map = _mtp_nested_replace_stubs(monkeypatch, bin_modified=None)
    src = tmp_path / 'Game'
    src.mkdir()
    (src / 'a.bin').write_bytes(b'new')
    source = str(src)
    stamp = ops.preview_import([source], root_uri, True)['items'][0]['conflict']['stamp']
    result = ops.apply_add([source], root_uri, True, decisions={source: {'action': 'replace', 'stamp': stamp}})
    assert result['kept'] == [{'name': 'Game', 'reason': 'unverifiable'}]
    assert result['replaced'] == []


def test_publish_flush_failure_reports_failure_not_success(tmp_path, monkeypatch):
    """Closure A: a failing final fsync must not surface as a copied file."""
    dest = tmp_path / 'dest'
    dest.mkdir()
    src = tmp_path / 'src'
    src.mkdir()
    (src / 'free.sfc').write_bytes(b'new')
    _no_renamex(monkeypatch)
    monkeypatch.setattr(ops.os, 'link',
                        lambda *args: (_ for _ in ()).throw(OSError('no links on this volume')))
    monkeypatch.setattr(ops.os, 'fsync',
                        lambda fd: (_ for _ in ()).throw(OSError(28, 'No space left on device')))
    source = str(src / 'free.sfc')
    result = ops.apply_add([source], str(dest), False, decisions={source: {'action': 'copy'}})
    assert result['copied'] == [] and result['replaced'] == []
    assert result['failed'] and 'free.sfc' == result['failed'][0]['name']
    # The claimed placeholder is left and reported — path deletion could not
    # prove it still refers to our object, so nothing is unlinked by name.
    assert (dest / 'free.sfc').exists()
    assert any(entry['path'] == str(dest / 'free.sfc') for entry in result['leftovers'])
    assert not list(dest.glob('*.plcn-*'))


def test_cleanup_never_unlinks_a_swapped_foreign_file(tmp_path, monkeypatch):
    """Closure A: a write failure's cleanup cannot delete a foreign claimant."""
    dest = tmp_path / 'dest'
    dest.mkdir()
    src = tmp_path / 'src'
    src.mkdir()
    (src / 'free.sfc').write_bytes(b'new')
    _no_renamex(monkeypatch)
    monkeypatch.setattr(ops.os, 'link',
                        lambda *args: (_ for _ in ()).throw(OSError('no links on this volume')))
    target = dest / 'free.sfc'

    def fail_and_swap(source, sink, *args):
        # A foreign file takes the claimed name, then the write fails.
        target.unlink()
        target.write_bytes(b'outside')
        raise OSError('write failed')

    monkeypatch.setattr(ops.shutil, 'copyfileobj', fail_and_swap)
    source = str(src / 'free.sfc')
    result = ops.apply_add([source], str(dest), False, decisions={source: {'action': 'copy'}})
    assert target.read_bytes() == b'outside'
    assert result['failed'] and result['copied'] == []
    assert not list(dest.glob('*.plcn-*'))


def test_publish_conflict_reports_written_paths_and_the_kept_member(tmp_path, monkeypatch):
    """Closure A: a member conflict entry names the real completed range."""
    dest = tmp_path / 'dest'
    dest.mkdir()
    src = tmp_path / 'src'
    (src / 'Pack').mkdir(parents=True)
    (src / 'Pack' / 'z.bin').write_bytes(b'new-z')
    (src / 'Pack' / 'a.bin').write_bytes(b'new-a')
    _no_renamex(monkeypatch)
    real_mkdir = os.mkdir
    injected = []

    def fake_mkdir(path, mode=0o777):
        real_mkdir(path, mode)
        if not injected and Path(path).name == 'Pack':
            injected.append(path)
            (Path(path) / 'z.bin').write_bytes(b'outside-z')

    monkeypatch.setattr(os, 'mkdir', fake_mkdir)
    source = str(src / 'Pack')
    result = ops.apply_add([source], str(dest), False, decisions={source: {'action': 'copy'}})
    assert (dest / 'Pack' / 'z.bin').read_bytes() == b'outside-z'
    failure = result['failed'][0]
    assert failure['name'] == 'Pack' and failure['conflict'] == str(dest / 'Pack' / 'z.bin')
    assert failure['target'] == str(dest / 'Pack')
    # Members published before the conflict are reported with real paths —
    # transient ._ sidecars never appear as either written or conflicted.
    assert failure['written'] == [str(dest / 'Pack' / 'a.bin')]
    assert (dest / 'Pack' / 'a.bin').read_bytes() == b'new-a'
    assert not list(dest.glob('*.plcn-*'))


def test_stamp_recheck_detects_a_member_edited_during_itself(tmp_path, monkeypatch):
    """Closure B: rewriting member-1 while member-2 is digested is caught."""
    dest = tmp_path / 'dest'
    (dest / 'Game').mkdir(parents=True)
    (dest / 'Game' / 'a.bin').write_bytes(b'first')
    (dest / 'Game' / 'z.bin').write_bytes(b'second')
    src = tmp_path / 'src'
    (src / 'Game').mkdir(parents=True)
    (src / 'Game' / 'new.bin').write_bytes(b'new')
    source = str(src / 'Game')
    stamp = ops.preview_import([source], str(dest), False)['items'][0]['conflict']['stamp']
    real_digest = ops._file_digest

    def hook(path):
        # While member-2 is hashed, member-1 changes under our feet.
        if Path(path).name == 'z.bin':
            (dest / 'Game' / 'a.bin').write_bytes(b'first-but-longer')
        return real_digest(path)

    monkeypatch.setattr(ops, '_file_digest', hook)
    result = ops.apply_add([source], str(dest), False, decisions={source: {'action': 'replace', 'stamp': stamp}})
    assert result['kept'] == [{'name': 'Game', 'reason': 'unverifiable'}]
    assert result['replaced'] == []
    assert (dest / 'Game' / 'a.bin').read_bytes() == b'first-but-longer'
    assert not list(dest.glob('*.plcn-*'))


def test_replace_keeps_a_foreign_target_appearing_after_parking(tmp_path, monkeypatch):
    """Closure B: a new object claiming the freed name is never renamed over."""
    dest = tmp_path / 'dest'
    dest.mkdir()
    original = dest / 'game.sfc'
    original.write_bytes(b'old')
    src = tmp_path / 'src'
    src.mkdir()
    (src / 'game.sfc').write_bytes(b'new')
    _no_renamex(monkeypatch)
    real_exclusive = ops._rename_exclusive
    calls = []

    def hook(staged, target):
        calls.append(staged)
        if len(calls) == 1:
            # The freed name gets a foreign claimant before staging lands.
            Path(target).write_bytes(b'outside')
        return real_exclusive(staged, target)

    monkeypatch.setattr(ops, '_rename_exclusive', hook)
    source = str(src / 'game.sfc')
    stamp = ops.preview_import([source], str(dest), False)['items'][0]['conflict']['stamp']
    result = ops.apply_add([source], str(dest), False, decisions={source: {'action': 'replace', 'stamp': stamp}})
    assert original.read_bytes() == b'outside'
    assert result['replaced'] == []
    failure = result['failed'][0]
    assert failure['recoverable'] and any('.plcn-old-' in path for path in failure['recoverable'])
    # The parked original and the staged copy remain for manual salvage.
    parked = [path for path in failure['recoverable'] if '.plcn-old-' in path][0]
    assert Path(parked).read_bytes() == b'old'


def test_replace_rolls_back_when_publish_fails_without_a_claimant(tmp_path, monkeypatch):
    """Closure B: a plain publish failure restores the parked original."""
    dest = tmp_path / 'dest'
    dest.mkdir()
    original = dest / 'game.sfc'
    original.write_bytes(b'old')
    src = tmp_path / 'src'
    src.mkdir()
    (src / 'game.sfc').write_bytes(b'new')
    real_exclusive = ops._rename_exclusive
    calls = []

    def hook(staged, target):
        calls.append(staged)
        if len(calls) == 1:
            raise OSError('disk vanished')
        return real_exclusive(staged, target)

    monkeypatch.setattr(ops, '_rename_exclusive', hook)
    source = str(src / 'game.sfc')
    stamp = ops.preview_import([source], str(dest), False)['items'][0]['conflict']['stamp']
    result = ops.apply_add([source], str(dest), False, decisions={source: {'action': 'replace', 'stamp': stamp}})
    assert original.read_bytes() == b'old'
    assert result['replaced'] == [] and result['failed']
    assert not list(dest.glob('*.plcn-*'))


def test_cleanup_after_identity_check_never_unlinks_a_claimant(tmp_path, monkeypatch):
    """A-01: a claimant swapped in after the identity check keeps its bytes.

    POSIX cannot unlink by held identity, so the failed claim leaves the path
    and reports it for verification instead of deleting by name."""
    dest = tmp_path / 'dest'
    dest.mkdir()
    src = tmp_path / 'src'
    src.mkdir()
    (src / 'free.sfc').write_bytes(b'new')
    _no_renamex(monkeypatch)
    monkeypatch.setattr(ops.os, 'link',
                        lambda *args: (_ for _ in ()).throw(OSError('no links on this volume')))
    monkeypatch.setattr(ops.os, 'fsync',
                        lambda fd: (_ for _ in ()).throw(OSError(28, 'No space left on device')))
    target = dest / 'free.sfc'
    unlinked = []
    real_unlink = os.unlink

    def spy_unlink(path, *args):
        unlinked.append(str(path))
        return real_unlink(path, *args)

    real_stat = os.stat

    def stat_then_swap(path, *args, **kwargs):
        result = real_stat(path, *args, **kwargs)
        if str(path) == str(target):
            # A claimant takes the name right after the identity check.
            target.write_bytes(b'outside')
        return result

    monkeypatch.setattr(ops.os, 'unlink', spy_unlink)
    monkeypatch.setattr(ops.os, 'stat', stat_then_swap)
    source = str(src / 'free.sfc')
    result = ops.apply_add([source], str(dest), False, decisions={source: {'action': 'copy'}})
    assert target.read_bytes() == b'outside'
    assert str(target) not in unlinked
    assert result['failed'] and result['copied'] == []
    # The path is reported as leftover evidence, not silently cleaned.
    assert any(entry['path'] == str(target) for entry in result['leftovers'])


def test_stamp_recheck_detects_equal_size_same_mtime_edit(tmp_path, monkeypatch):
    """B-01: an equal-length, same-mtime rewrite during digest is caught."""
    dest = tmp_path / 'dest'
    (dest / 'Game').mkdir(parents=True)
    keep = dest / 'Game' / 'a.bin'
    keep.write_bytes(b'first')
    (dest / 'Game' / 'z.bin').write_bytes(b'second')
    src = tmp_path / 'src'
    (src / 'Game').mkdir(parents=True)
    (src / 'Game' / 'new.bin').write_bytes(b'new')
    source = str(src / 'Game')
    stamp = ops.preview_import([source], str(dest), False)['items'][0]['conflict']['stamp']
    real_digest = ops._file_digest
    mtime = keep.stat().st_mtime_ns
    rewritten = []

    def hook(path):
        # While member-2 is hashed, member-1 is rewritten with the same
        # length and the same mtime — metadata alone cannot see this.
        if Path(path).name == 'z.bin' and not rewritten:
            rewritten.append(True)
            keep.write_bytes(b'FIRST')
            os.utime(keep, ns=(keep.stat().st_atime_ns, mtime))
        return real_digest(path)

    monkeypatch.setattr(ops, '_file_digest', hook)
    result = ops.apply_add([source], str(dest), False, decisions={source: {'action': 'replace', 'stamp': stamp}})
    assert rewritten
    assert result['kept'] == [{'name': 'Game', 'reason': 'unverifiable'}]
    assert result['replaced'] == []
    assert keep.read_bytes() == b'FIRST'
    assert not list(dest.glob('*.plcn-*'))


def test_parked_original_edited_before_cleanup_is_kept(tmp_path, monkeypatch):
    """B-01: an original edited after parking is not authorized for delete."""
    dest = tmp_path / 'dest'
    (dest / 'Game').mkdir(parents=True)
    (dest / 'Game' / 'a.bin').write_bytes(b'aaaa')
    src = tmp_path / 'src'
    (src / 'Game').mkdir(parents=True)
    (src / 'Game' / 'b.bin').write_bytes(b'new')
    source = str(src / 'Game')
    stamp = ops.preview_import([source], str(dest), False)['items'][0]['conflict']['stamp']
    real_exclusive = ops._rename_exclusive
    calls = []

    def hook(staged, target):
        calls.append(staged)
        if len(calls) == 1:
            # The parked original is edited before cleanup can prove it.
            parked = next(dest.glob('Game.plcn-old-*'))
            (parked / 'a.bin').write_bytes(b'zzzz')
        return real_exclusive(staged, target)

    monkeypatch.setattr(ops, '_rename_exclusive', hook)
    result = ops.apply_add([source], str(dest), False, decisions={source: {'action': 'replace', 'stamp': stamp}})
    # The new tree published, and the edited original is reported where it sits.
    assert result['replaced'] == ['Game']
    assert (dest / 'Game' / 'b.bin').read_bytes() == b'new'
    assert result['leftovers']
    parked = next(dest.glob('Game.plcn-old-*'))
    assert result['leftovers'][0]['path'] == str(parked)
    assert (parked / 'a.bin').read_bytes() == b'zzzz'


def test_dir_publish_abort_reports_written_and_interrupted_member(tmp_path, monkeypatch):
    """A-02: a plain member failure keeps and reports the published prefix."""
    dest = tmp_path / 'dest'
    dest.mkdir()
    src = tmp_path / 'src'
    (src / 'Pack').mkdir(parents=True)
    (src / 'Pack' / 'a.bin').write_bytes(b'new-a')
    (src / 'Pack' / 'b.bin').write_bytes(b'new-b')
    (src / 'Pack' / 'c.bin').write_bytes(b'new-c')
    _no_renamex(monkeypatch)
    monkeypatch.setattr(ops.os, 'link',
                        lambda *args: (_ for _ in ()).throw(OSError('no links on this volume')))
    real_copyobj = ops.shutil.copyfileobj
    calls = []

    def copy_then_fail(source_fd, sink_fd, *args):
        calls.append(1)
        if len(calls) == 2:
            raise OSError('I/O error')
        return real_copyobj(source_fd, sink_fd, *args)

    monkeypatch.setattr(ops.shutil, 'copyfileobj', copy_then_fail)
    source = str(src / 'Pack')
    result = ops.apply_add([source], str(dest), False, decisions={source: {'action': 'copy'}})
    failure = result['failed'][0]
    assert failure['name'] == 'Pack' and failure['target'] == str(dest / 'Pack')
    assert failure['written'] == [str(dest / 'Pack' / 'a.bin')]
    assert failure['interrupted'] == str(dest / 'Pack' / 'b.bin')
    assert 'I/O error' in failure['error']
    assert (dest / 'Pack' / 'a.bin').read_bytes() == b'new-a'
    assert not (dest / 'Pack' / 'c.bin').exists()
    # The partial b.bin claim is left and reported, never deleted by name.
    assert any(entry['path'] == str(dest / 'Pack' / 'b.bin') for entry in result['leftovers'])
    assert not list(dest.glob('*.plcn-*'))


def test_nested_dir_written_paths_list_actual_files(tmp_path, monkeypatch):
    """A-02: a completed subdirectory contributes its real file paths."""
    dest = tmp_path / 'dest'
    dest.mkdir()
    src = tmp_path / 'src'
    (src / 'Pack' / 'A').mkdir(parents=True)
    (src / 'Pack' / 'A' / 'a.bin').write_bytes(b'a')
    (src / 'Pack' / 'b.bin').write_bytes(b'b')
    (src / 'Pack' / 'c.bin').write_bytes(b'c')
    _no_renamex(monkeypatch)
    monkeypatch.setattr(ops.os, 'link',
                        lambda *args: (_ for _ in ()).throw(OSError('no links on this volume')))
    real_copyobj = ops.shutil.copyfileobj
    calls = []

    def copy_then_fail(source_fd, sink_fd, *args):
        calls.append(1)
        if len(calls) == 2:
            raise OSError('I/O error')
        return real_copyobj(source_fd, sink_fd, *args)

    monkeypatch.setattr(ops.shutil, 'copyfileobj', copy_then_fail)
    source = str(src / 'Pack')
    result = ops.apply_add([source], str(dest), False, decisions={source: {'action': 'copy'}})
    failure = result['failed'][0]
    # Sorted order: A/a.bin publishes fully, b.bin aborts, c.bin never ran.
    assert failure['written'] == [str(dest / 'Pack' / 'A' / 'a.bin')]
    assert failure['interrupted'] == str(dest / 'Pack' / 'b.bin')
    assert (dest / 'Pack' / 'A' / 'a.bin').read_bytes() == b'a'
    assert not (dest / 'Pack' / 'c.bin').exists()


def test_open_handle_write_after_publish_keeps_parked_bytes(tmp_path):
    """B-01: a handle held across the park still writes through — the parked
    original keeps those bytes and is reported where it actually sits."""
    dest = tmp_path / 'dest'
    (dest / 'Game').mkdir(parents=True)
    old = dest / 'Game' / 'old.bin'
    old.write_bytes(b'old')
    src = tmp_path / 'src'
    (src / 'Game').mkdir(parents=True)
    (src / 'Game' / 'b.bin').write_bytes(b'new')
    source = str(src / 'Game')
    stamp = ops.preview_import([source], str(dest), False)['items'][0]['conflict']['stamp']
    fd = os.open(old, os.O_RDWR)
    result = ops.apply_add([source], str(dest), False, decisions={source: {'action': 'replace', 'stamp': stamp}})
    os.write(fd, b'live')
    os.fsync(fd)
    os.close(fd)
    parked = next(dest.glob('Game.plcn-old-*'))
    assert (parked / 'old.bin').read_bytes() == b'live'
    assert result['replaced'] == ['Game']
    assert any(entry['path'] == str(parked) for entry in result['leftovers'])
    assert (dest / 'Game' / 'b.bin').read_bytes() == b'new'
