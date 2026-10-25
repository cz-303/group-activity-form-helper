"""Consistent SQLite backups; validated restore is applied before any workers start."""

from pathlib import Path
from zipfile import ZipFile, ZIP_DEFLATED
import json, sqlite3, tempfile, time, os, hashlib, shutil
from storage_io import atomic_bytes, atomic_text
from backup_validation import validate_database


def make_backup(store, data, config, kind='auto', keep=10):
    data = Path(data)
    folder = data / 'backups'
    folder.mkdir(exist_ok=True)
    name = f'{kind}-{time.strftime("%Y%m%d-%H%M%S")}-{time.time_ns() % 1000000:06d}.zip'
    target = folder / name
    with tempfile.TemporaryDirectory(dir=folder) as tmp:
        dbfile = Path(tmp) / 'tasks.sqlite3'
        source = sqlite3.connect(Path(store.path).resolve().as_uri() + '?mode=ro', uri=True, timeout=10)
        dest = None
        try:
            dest = sqlite3.connect(dbfile)
            until = time.monotonic() + 60

            def progress(status, remaining, total):
                if time.monotonic() > until:
                    raise TimeoutError('Backup timed out')

            source.backup(dest, pages=256, progress=progress, sleep=0.05)
            # Runtime-only acceleration is recreated on startup. Omit it from
            # portable backups so existing v0.11 validators can still read them.
            with dest:
                dest.execute('DROP INDEX IF EXISTS notices_by_url')
                dest.execute('DROP INDEX IF EXISTS tasks_by_link_identity')
            count = dest.execute('SELECT COUNT(*) FROM tasks').fetchone()[0]
        finally:
            source.close()
            if dest is not None:
                dest.close()
        payload = {
            'tasks.sqlite3': dbfile.read_bytes(),
            'config.json': json.dumps(config, ensure_ascii=False, indent=2).encode('utf-8'),
        }
        manifest = {
            'format': 'group-form-helper-backup-1',
            'created': time.time(),
            'tasks': count,
            'sha256': {k: hashlib.sha256(v).hexdigest() for k, v in payload.items()},
        }
        draft = Path(tmp) / 'archive.zip'
        with ZipFile(draft, 'w', ZIP_DEFLATED) as z:
            for k, v in payload.items():
                z.writestr(k, v)
            z.writestr('manifest.json', json.dumps(manifest))
        os.replace(draft, target)
    if kind == 'auto':
        for old in sorted(folder.glob('auto-*.zip'), key=lambda p: p.stat().st_mtime, reverse=True)[keep:]:
            old.unlink()
    return target


def inspect_backup(path, validate_config):
    with ZipFile(path) as z:
        if len(z.infolist()) != 3 or set(z.namelist()) != {'tasks.sqlite3', 'config.json', 'manifest.json'}:
            raise ValueError('不是支持的助手备份文件。')
        limits = {'tasks.sqlite3': 200 * 1024 * 1024, 'config.json': 1024 * 1024, 'manifest.json': 65536}
        if any(i.file_size > limits[i.filename] for i in z.infolist()):
            raise ValueError('备份文件过大。')
        manifest = json.loads(z.read('manifest.json'))
        payload = {k: z.read(k) for k in ('tasks.sqlite3', 'config.json')}
    if not isinstance(manifest, dict) or manifest.get('format') != 'group-form-helper-backup-1':
        raise ValueError('不支持此备份版本。')
    if (
        not isinstance(manifest.get('sha256'), dict)
        or not isinstance(manifest.get('created'), (int, float))
        or not 0 <= manifest['created'] <= 253402214400
    ):
        raise ValueError('备份说明无效。')
    for k, v in payload.items():
        if hashlib.sha256(v).hexdigest() != manifest['sha256'].get(k):
            raise ValueError('备份校验失败：' + k)
    config = validate_config(json.loads(payload['config.json']))
    with tempfile.TemporaryDirectory() as tmp:
        p = Path(tmp) / 'db'
        p.write_bytes(payload['tasks.sqlite3'])
        try:
            count = validate_database(p)
        except (sqlite3.Error, TypeError, KeyError, OverflowError) as error:
            raise ValueError('备份中的任务数据结构不完整或内容无效。') from error
        payload['tasks.sqlite3'] = p.read_bytes()
    return manifest, payload, config, count


def stage_restore(path, data, validate_config):
    # Re-validate immediately before staging; no extraction of archive paths.
    data = Path(data)
    with tempfile.TemporaryDirectory(dir=data) as folder:
        temp = Path(folder) / 'pending.zip'
        shutil.copyfile(path, temp)
        inspect_backup(temp, validate_config)
        os.replace(temp, data / 'restore_pending.zip')


def apply_pending(data, validate_config):
    data = Path(data)
    pending = data / 'restore_pending.zip'
    journal = data / 'restore_journal.json'
    rolled_back = False

    def install(payload):
        for name, value in payload.items():
            atomic_bytes(data / name, value)

    # Recover the original pair if the preceding restore was interrupted.
    if journal.exists():
        info = json.loads(journal.read_text(encoding='utf-8'))
        if (
            not isinstance(info, dict)
            or not isinstance(info.get('safety'), str)
            or Path(info['safety']).name != info['safety']
        ):
            raise ValueError('恢复记录损坏，请保留data目录并从恢复前副本修复。')
        backup = data / 'backups' / info['safety']
        _, payload, old_config, _ = inspect_backup(backup, validate_config)
        old_config['auto_resume'] = False
        payload['config.json'] = json.dumps(old_config, ensure_ascii=False, indent=2).encode('utf-8')
        install(payload)
        journal.unlink()
        rolled_back = True
    if not pending.exists():
        return '上次恢复被中断，已回退到恢复前副本；任务处理已暂停，请核对数据。' if rolled_back else ''
    _, payload, config, count = inspect_backup(pending, validate_config)
    # Never resume forms or monitoring automatically as a side effect of recovery.
    config['auto_resume'] = False
    payload['config.json'] = json.dumps(config, ensure_ascii=False, indent=2).encode('utf-8')
    from task_store import TaskStore

    existing = TaskStore(data / 'tasks.sqlite3')
    config_path = data / 'config.json'
    try:
        old_config = (
            validate_config(json.loads(config_path.read_text(encoding='utf-8')))
            if config_path.exists()
            else validate_config({})
        )
    except (ValueError, TypeError):
        folder = data / 'backups'
        folder.mkdir(exist_ok=True)
        atomic_bytes(folder / f'config-invalid-{time.time_ns()}.json', config_path.read_bytes())
        old_config = validate_config({})
        old_config['auto_resume'] = False
    safety = make_backup(existing, data, old_config, 'before-restore')
    with atomic_text(journal) as stream:
        json.dump({'safety': safety.name}, stream)
    try:
        install(payload)
    except Exception:
        _, original, _, _ = inspect_backup(safety, validate_config)
        install(original)
        journal.unlink()
        raise
    # Removing pending first permits harmless rollback if interrupted before journal removal.
    pending.unlink()
    journal.unlink()
    return f'已恢复 {count} 项任务；恢复前副本已保存。任务处理已暂停，请核对后手动继续。'
