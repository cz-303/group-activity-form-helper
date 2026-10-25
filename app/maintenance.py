"""One background maintenance batch. No UI imports or browser operations."""

from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from reminders import collect_due_alerts
from task_store import wake_due_tasks, has_due_tasks
from open_policy import queue_filter


@dataclass
class MaintenanceResult:
    alerts: list
    due: bool
    woke: int
    backup_due: bool = False
    backup_error: Exception | None = None


def run_maintenance(
    store, data, config, system_alerts=(), *, check_reminders=True, check_backup=False, now=None, manual_urls=()
):
    now = now or datetime.now()
    stamp = now.timestamp()
    # All task/alert changes either commit together or roll back together.
    with store.connection(timeout=0.2) as db:
        woke = wake_due_tasks(db, stamp)
        alerts = []
        for key, title, body in system_alerts:
            inserted = db.execute(
                'INSERT OR IGNORE INTO alerts(id,title,body,created) VALUES(?,?,?,?)', (key, title, body, stamp)
            ).rowcount
            if inserted:
                alerts.append((title, body))
        if check_reminders:
            alerts.extend(collect_due_alerts(db, config, now))
        due = has_due_tasks(db, stamp, queue_filter(config, manual_urls))
    result = MaintenanceResult(alerts, due, woke)
    # A backup-directory failure must not discard successfully committed alerts.
    if check_backup and config['backup_enabled']:
        try:
            latest = max((p.stat().st_mtime for p in (Path(data) / 'backups').glob('auto-*.zip')), default=0)
            result.backup_due = stamp - latest >= config['backup_hours'] * 3600
        except OSError as error:
            result.backup_error = error
    return result
