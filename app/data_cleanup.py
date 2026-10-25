"""Preview-bound cleanup. No recursive deletion, no implicit user-data reset."""

from dataclasses import dataclass
from pathlib import Path
import hashlib
import json
import re
import time
from backup_support import make_backup

AUTO_NAME = re.compile(r'auto-\d{8}-\d{6}-\d{6}\.zip\Z')


@dataclass(frozen=True)
class CleanupOptions:
    days: int = 30
    tasks: bool = False
    alerts: bool = False
    backups: bool = False
    logs: bool = False
    safety_backup: bool = True

    def validate(self):
        if type(self.days) is not int or self.days not in (7, 30, 90, 180):
            raise ValueError('请选择7、30、90或180天。')
        if any(type(getattr(self, key)) is not bool for key in ('tasks', 'alerts', 'backups', 'logs', 'safety_backup')):
            raise ValueError('清理选项格式无效。')


@dataclass(frozen=True)
class CleanupPlan:
    root: str
    options: CleanupOptions
    cutoff: float
    task_urls: tuple
    notice_count: int
    alert_ids: tuple
    files: tuple
    fingerprint: str


def checked_root(data):
    root = Path(data).absolute()
    if root.is_symlink() or root.is_junction() or not root.is_dir():
        raise ValueError('数据目录不可用，或为链接目录，无法安全清理。')
    return root.resolve()


def file_signature(root, relative):
    path = root / relative
    if path.parent.is_symlink() or path.parent.is_junction() or path.is_symlink() or path.is_junction():
        raise ValueError('清理范围中有链接文件或目录，请移除链接后重新预览。')
    if not path.resolve().is_relative_to(root) or not path.is_file():
        raise ValueError('清理文件范围已改变，请重新预览。')
    stat = path.stat()
    return (relative, stat.st_size, stat.st_mtime_ns, stat.st_ino)


def inventory(db, root, options, cutoff):
    tasks = []
    notices = []
    alerts = []
    files = []
    if options.tasks:
        tasks = [
            dict(r)
            for r in db.execute(
                "SELECT * FROM tasks WHERE updated<? AND state!='opening' AND disposition!='snoozed' AND (state='done' OR disposition='ignored') ORDER BY url",
                (cutoff,),
            )
        ]
        for row in tasks:
            notices.extend(dict(r) for r in db.execute('SELECT * FROM notices WHERE url=? ORDER BY id', (row['url'],)))
    if options.alerts:
        alerts = [
            dict(r) for r in db.execute('SELECT * FROM alerts WHERE is_read=1 AND created<? ORDER BY id', (cutoff,))
        ]
    if options.backups:
        folder = root / 'backups'
        if folder.is_symlink() or folder.is_junction():
            raise ValueError('备份目录是链接目录，无法安全清理。')
        candidates = [
            file_signature(root, str(p.relative_to(root)))
            for p in folder.glob('auto-*.zip')
            if AUTO_NAME.fullmatch(p.name)
        ]
        candidates.sort(key=lambda item: (item[2], item[0]), reverse=True)
        # Always retain at least two recent auto backups, even when all are old.
        files.extend(item for item in candidates[2:] if item[2] / 1e9 < cutoff)
    if options.logs:
        for name in ('diagnostics.log.1', 'diagnostics.log.2'):
            if (root / name).exists() or (root / name).is_symlink():
                item = file_signature(root, name)
                if item[2] / 1e9 < cutoff:
                    files.append(item)
    files = tuple(sorted(files))
    raw = json.dumps([tasks, notices, alerts, files], ensure_ascii=False, sort_keys=True).encode('utf-8')
    return tasks, notices, alerts, files, hashlib.sha256(raw).hexdigest()


def preview_cleanup(store, data, options, now=None):
    options.validate()
    root = checked_root(data)
    cutoff = (time.time() if now is None else now) - options.days * 86400
    with store.connection() as db:
        db.execute('BEGIN')
        tasks, notices, alerts, files, fingerprint = inventory(db, root, options, cutoff)
    return CleanupPlan(
        str(root),
        options,
        cutoff,
        tuple(r['url'] for r in tasks),
        len(notices),
        tuple(r['id'] for r in alerts),
        files,
        fingerprint,
    )


def execute_cleanup(store, data, config, plan):
    if not isinstance(plan, CleanupPlan):
        raise ValueError('请先生成清理预览。')
    plan.options.validate()
    root = checked_root(data)
    if str(root) != plan.root or Path(store.path).resolve() != root / 'tasks.sqlite3':
        raise ValueError('清理预览不属于当前数据目录。')
    if (root / 'restore_pending.zip').exists() or (root / 'restore_journal.json').exists():
        raise ValueError('当前有待恢复数据，请先完成恢复再清理。')
    safety = None

    def matches(current):
        tasks, notices, alerts, files, fingerprint = current
        return (
            fingerprint == plan.fingerprint
            and files == plan.files
            and tuple(r['url'] for r in tasks) == plan.task_urls
            and len(notices) == plan.notice_count
            and tuple(r['id'] for r in alerts) == plan.alert_ids
        )

    # First reject stale previews before creating a potentially large safety copy.
    with store.connection() as db:
        if not matches(inventory(db, root, plan.options, plan.cutoff)):
            raise ValueError('数据或文件在预览后发生变化，未执行清理；请重新预览。')
    if (plan.task_urls or plan.alert_ids) and plan.options.safety_backup:
        safety = make_backup(store, root, config, 'before-cleanup')
    with store.connection() as db:
        db.execute('BEGIN IMMEDIATE')
        current = inventory(db, root, plan.options, plan.cutoff)
        tasks = current[0]
        if not matches(current):
            raise ValueError('数据或文件在预览后发生变化，未执行清理；请重新预览。')
        for row in tasks:
            db.execute('DELETE FROM task_attachments WHERE url=?', (row['url'],))
            db.execute('DELETE FROM notices WHERE url=?', (row['url'],))
            key = hashlib.sha256((row['url'] + '\0' + row['deadline']).encode()).hexdigest()
            db.execute('DELETE FROM reminder_keys WHERE id LIKE ?', (key + ':%',))
            db.execute('DELETE FROM tasks WHERE url=?', (row['url'],))
        for key in plan.alert_ids:
            db.execute('DELETE FROM alerts WHERE id=?', (key,))
    deleted = []
    failed = []
    for item in plan.files:
        try:
            # Validate absolute target and unchanged metadata immediately before unlink.
            checked_root(data)
            if file_signature(root, item[0]) != item:
                raise ValueError('file changed')
            (root / item[0]).unlink()
            deleted.append(item[0])
        except (OSError, ValueError):
            failed.append(item[0])
    return {
        'tasks': len(plan.task_urls),
        'notices': plan.notice_count,
        'alerts': len(plan.alert_ids),
        'files': deleted,
        'failed': failed,
        'safety': str(safety) if safety else '',
    }


def describe_plan(plan):
    size = sum(item[1] for item in plan.files)
    text = (
        f'清理范围：早于 {time.strftime("%Y-%m-%d", time.localtime(plan.cutoff))}\n'
        f'已处理/已忽略任务：{len(plan.task_urls)} 项；关联通知：{plan.notice_count} 条\n'
        f'已读提醒：{len(plan.alert_ids)} 条\n文件：{len(plan.files)} 个，合计 {size / 1024 / 1024:.2f} MB\n'
        '（数据库删除不承诺立即缩小文件大小）\n\n'
    )
    text += '任务链接：\n' + ('\n'.join(plan.task_urls[:100]) or '无')
    if len(plan.task_urls) > 100:
        text += f'\n另有 {len(plan.task_urls) - 100} 项，共 {len(plan.task_urls)} 项。'
    text += '\n\n文件清单：\n' + ('\n'.join(item[0] for item in plan.files) or '无')
    return text
