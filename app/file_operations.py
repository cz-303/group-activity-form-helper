"""Background-safe file workflows. No UI imports and no live data replacement."""

from dataclasses import dataclass
from pathlib import Path
import csv
import json
import shutil
import sqlite3
import tempfile
import time
from backup_support import inspect_backup, make_backup, stage_restore
from settings import validate_settings
from storage_io import atomic_bytes, atomic_text, spreadsheet_cell
from task_store import TaskStore, state_label


@dataclass
class PreparedRestore:
    directory: object
    archive: Path
    created: float
    count: int
    retained: bool = False

    def close(self):
        self.directory.cleanup()

    def discard_unless_retained(self):
        if not self.retained:
            self.close()


def prepare_restore(path):
    directory = tempfile.TemporaryDirectory(prefix='activity-restore-')
    try:
        copy = Path(directory.name) / 'snapshot.zip'
        shutil.copyfile(path, copy)
        manifest, _, _, count = inspect_backup(copy, validate_settings)
        return PreparedRestore(directory, copy, manifest['created'], count)
    except Exception:
        directory.cleanup()
        raise


def prepare_old_folder(folder):
    directory = tempfile.TemporaryDirectory(prefix='activity-migration-')
    try:
        archive, count = snapshot_old_folder(folder, directory.name, validate_settings)
        manifest, _, _, _ = inspect_backup(archive, validate_settings)
        return PreparedRestore(directory, archive, manifest['created'], count)
    except Exception:
        directory.cleanup()
        raise


def install_pending(prepared, data):
    try:
        stage_restore(prepared.archive, data, validate_settings)
    finally:
        prepared.close()


def export_migration(store, data, config, target):
    target = Path(target)
    if target.suffix.lower() != '.zip' or target.resolve().is_relative_to(Path(data).resolve()):
        raise ValueError('请将迁移包保存为软件 data 目录以外的 ZIP 文件，以免覆盖工作数据或备份。')
    archive = make_backup(store, data, config, 'migration')
    atomic_bytes(target, archive.read_bytes())
    return str(target)


def export_tasks(database, target):
    target = Path(target)
    if target.suffix.lower() != '.csv' or target.resolve() == Path(database).resolve():
        raise ValueError('请另存为 CSV 文件，不要覆盖任务数据库或软件文件。')
    # Independent read transaction: do not hold the store's UI/monitor Python lock.
    db = sqlite3.connect(Path(database).resolve().as_uri() + '?mode=ro', uri=True, timeout=10)
    db.row_factory = sqlite3.Row
    try:
        with atomic_text(target, encoding='utf-8-sig', newline='') as stream:
            writer = csv.writer(stream)
            writer.writerow(
                [
                    '活动标题',
                    '状态',
                    '链接',
                    '来源',
                    '发现时间',
                    '活动开始',
                    '活动结束',
                    '报名截止',
                    '参加意向',
                    '活动备注',
                    '准备清单',
                    '提醒模式',
                    '专属提前量',
                    '尝试次数',
                    '说明',
                ]
            )
            count = 0
            for row in db.execute('SELECT * FROM tasks ORDER BY created DESC'):
                row = dict(row)
                checklist = '\n'.join(
                    ('已完成 ' if x['done'] else '待准备 ') + x['text'] for x in json.loads(row['checklist'])
                )
                writer.writerow(
                    map(
                        spreadsheet_cell,
                        [
                            row['title'],
                            state_label(row),
                            row['url'],
                            row['source'],
                            time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(row['created'])),
                            row['event_start'],
                            row['event_end'],
                            row['deadline'],
                            row['intent'],
                            row['activity_notes'],
                            checklist,
                            row['reminder_mode'],
                            row['task_minutes'],
                            row['attempts'],
                            row['note'],
                        ],
                    )
                )
                count += 1
        return count
    finally:
        db.close()


def snapshot_old_folder(folder, temp, validate_config):
    folder = Path(folder)
    data = folder if (folder / 'tasks.sqlite3').is_file() else folder / 'data'
    if not (data / 'tasks.sqlite3').is_file():
        raise ValueError('没有找到 tasks.sqlite3，请选择旧版程序文件夹或它的data文件夹。')
    temp = Path(temp)
    temp.mkdir(exist_ok=True)
    source = sqlite3.connect((data / 'tasks.sqlite3').resolve().as_uri() + '?mode=ro', uri=True)
    dest = None
    try:
        dest = sqlite3.connect(temp / 'tasks.sqlite3')
        if source.execute('PRAGMA quick_check').fetchone()[0] != 'ok':
            raise ValueError('旧版任务数据库校验未通过。')
        cols = {r[1] for r in source.execute('PRAGMA table_info(tasks)')}
        if not {'url', 'source', 'created', 'state'}.issubset(cols):
            raise ValueError('不是支持的旧版任务数据。')
        source.backup(dest)
    finally:
        source.close()
        if dest is not None:
            dest.close()
    config = validate_config(
        json.loads((data / 'config.json').read_text(encoding='utf-8')) if (data / 'config.json').is_file() else {}
    )
    # Upgrade the copied database only; never alter the old installation.
    store = TaskStore(temp / 'tasks.sqlite3')
    archive = make_backup(store, temp, config, 'migration')
    inspect_backup(archive, validate_config)
    return archive, len(store.rows())
