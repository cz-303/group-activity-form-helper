"""Persist reminder keys so polling, restarts and clock corrections do not spam."""

import hashlib
import time
import json
from datetime import datetime
from activity_time import date_value


def parse_minutes(value):
    if isinstance(value, str):
        value = [v.strip() for v in value.replace('，', ',').split(',') if v.strip()]
    try:
        levels = sorted({int(v) for v in value}, reverse=True)
    except (ValueError, TypeError):
        raise ValueError('提前量需为分钟数，以逗号分隔，例如60,30。')
    if len(levels) > 10 or any(not 1 <= v <= 10080 for v in levels):
        raise ValueError('最多10个提前量，每个1—10080分钟。')
    return levels


def countdown(value, now=None):
    if not value:
        return '未设置截止'
    now = now or datetime.now()
    seconds = (date_value(value, True) - now).total_seconds()
    if seconds < 0:
        return '已截止'
    minutes = int(seconds // 60)
    if minutes < 1:
        return '不足1分钟'
    days, rest = divmod(minutes, 1440)
    hours, mins = divmod(rest, 60)
    return '剩余 ' + (f'{days}天 ' if days else '') + (f'{hours}小时 ' if hours else '') + f'{mins}分钟'


def due_alerts(store, config, now=None):
    with store.connection() as db:
        return collect_due_alerts(db, config, now)


def collect_due_alerts(db, config, now=None):
    """Caller owns the transaction, including persisted threshold keys."""
    now = now or datetime.now()
    result = []
    candidates = """SELECT * FROM tasks WHERE notice_revision>notice_alerted
        OR (disposition='active' AND state!='done'
            AND (deadline!='' OR remind_again_at IS NOT NULL))"""
    for row in db.execute(candidates).fetchall():
        title = row['title'] or '活动收集表'
        active = row['disposition'] == 'active' and row['state'] != 'done'
        mode = row['reminder_mode']
        enabled = mode == 'custom' or (mode == 'inherit' and config.get('deadline_reminders', True))
        if row['remind_again_at'] and row['remind_again_at'] <= now.timestamp() and active:
            if mode != 'off':
                key = f"again:{row['url']}:{row['remind_again_at']}"
                body = f"{title}\n这是你设置的再次提醒。{countdown(row['deadline'], now)}\n{row['url']}"
                if db.execute(
                    'INSERT OR IGNORE INTO alerts(id,title,body,created) VALUES(?,?,?,?)',
                    (key, '任务再次提醒', body, time.time()),
                ).rowcount:
                    result.append(('任务再次提醒', body))
            db.execute('UPDATE tasks SET remind_again_at=NULL WHERE url=?', (row['url'],))
        if row['notice_revision'] > row['notice_alerted']:
            if config.get('notify_updates', True) and active:
                key = f"update:{row['url']}:{row['notice_revision']}"
                body = (
                    f"{title}\n发现不同版本的原通知，请核对日期、地点和要求。保存的活动时间不会自动改变。\n{row['url']}"
                )
                if db.execute(
                    'INSERT OR IGNORE INTO alerts(id,title,body,created) VALUES(?,?,?,?)',
                    (key, '原通知有更新', body, time.time()),
                ).rowcount:
                    result.append(('原通知有更新', body))
            db.execute('UPDATE tasks SET notice_alerted=notice_revision WHERE url=?', (row['url'],))
        if not active or not enabled or not row['deadline']:
            continue
        seconds = (date_value(row['deadline'], True) - now).total_seconds()
        if seconds < 0:
            continue
        settings = json.loads(row['task_minutes']) if mode == 'custom' else config.get('deadline_minutes', [60, 30])
        levels = sorted({int(x) for x in settings if seconds <= int(x) * 60})
        if not levels:
            continue
        base = hashlib.sha256((row['url'] + '\0' + row['deadline']).encode()).hexdigest()
        missing = [
            m for m in levels if not db.execute('SELECT 1 FROM reminder_keys WHERE id=?', (f'{base}:{m}',)).fetchone()
        ]
        if not missing:
            continue
        for m in levels:
            db.execute('INSERT OR IGNORE INTO reminder_keys VALUES(?)', (f'{base}:{m}',))
        # If several levels were passed while offline, send one catch-up alert.
        key = f'deadline:{base}:{min(levels)}'
        body = f"{title} · {countdown(row['deadline'], now)}\n报名截止：{row['deadline']}\n请核对并手动提交。\n{row['url']}"
        if db.execute(
            'INSERT OR IGNORE INTO alerts(id,title,body,created) VALUES(?,?,?,?)',
            (key, '报名即将截止', body, time.time()),
        ).rowcount:
            result.append(('报名即将截止', body))
    return result


class MonitorHealth:
    def __init__(self):
        self.reset()

    def reset(self):
        self.started = None
        self.failed_since = None
        self.warned = False
        self.session = str(time.time_ns())

    def start(self, now=None):
        self.reset()
        self.started = time.monotonic() if now is None else now

    def observe(self, ok, now=None):
        now = time.monotonic() if now is None else now
        if ok:
            recovered = self.warned
            self.failed_since = None
            self.warned = False
            self.started = now
            return recovered
        if self.failed_since is None:
            self.failed_since = now
        return False

    def check(self, last_success_age, threshold, now=None, stale_threshold=None):
        now = time.monotonic() if now is None else now
        stale = last_success_age is not None and last_success_age >= (stale_threshold or threshold)
        failed = self.failed_since is not None and now - self.failed_since >= threshold
        startup = self.started is not None and last_success_age is None and now - self.started >= threshold
        if (stale or failed or startup) and not self.warned:
            self.warned = True
            return True
        return False
