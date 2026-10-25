"""Durable task queue. A task is retained even if opening fails or slots are full."""

import sqlite3
import threading
import time
import json
import hashlib
from pathlib import Path
from contextlib import contextmanager
from task_links import canonical_task_url as canonical_url
from task_links import task_identity
from activity_time import validate_dates
from notice_identity import ChatMessage


def wake_due_tasks(db, now):
    return db.execute(
        "UPDATE tasks SET disposition='active',snooze_until=NULL,updated=? WHERE disposition='snoozed' AND snooze_until<=?",
        (now, now),
    ).rowcount


def has_due_tasks(db, now, eligibility=None):
    clause, args = eligibility or ('1', ())
    return bool(
        db.execute(
            "SELECT 1 FROM tasks WHERE disposition='active' AND (state='pending' OR (state='failed' AND retry_at IS NOT NULL AND retry_at<=?)) AND ("
            + clause
            + ") LIMIT 1",
            (now, *args),
        ).fetchone()
    )


def query_matching_urls(db, query):
    """Shared parameterized search for both transactional and read-only callers."""
    terms = query.strip().split()
    if not terms:
        return None
    clause = "(instr(lower(t.title || char(10) || t.url || char(10) || t.source || char(10) || t.note || char(10) || t.activity_notes || char(10) || t.intent),lower(?))>0 OR EXISTS(SELECT 1 FROM notices n WHERE n.url=t.url AND instr(lower(n.body),lower(?))>0))"
    return {
        r['url']
        for r in db.execute(
            'SELECT t.url FROM tasks t WHERE ' + ' AND '.join(clause for _ in terms),
            [v for term in terms for v in (term, term)],
        )
    }


class TaskStore:
    def __init__(self, path):
        self.path = str(path)
        self.lock = threading.RLock()
        self._revision = 0
        with self.connection() as db:
            db.execute('BEGIN IMMEDIATE')
            db.execute('''CREATE TABLE IF NOT EXISTS tasks (
                url TEXT PRIMARY KEY, source TEXT NOT NULL, created REAL NOT NULL,
                updated REAL NOT NULL, state TEXT NOT NULL DEFAULT 'pending'
                CHECK(state IN ('pending','opening','opened','failed','done')),
                attempts INTEGER NOT NULL DEFAULT 0, retry_at REAL,
                note TEXT NOT NULL DEFAULT '')''')
            columns = {r['name'] for r in db.execute('PRAGMA table_info(tasks)')}
            for name, definition in {
                'disposition': "TEXT NOT NULL DEFAULT 'active'",
                'snooze_until': 'REAL',
                'title': "TEXT NOT NULL DEFAULT ''",
                'field_report': "TEXT NOT NULL DEFAULT ''",
                'event_start': "TEXT NOT NULL DEFAULT ''",
                'event_end': "TEXT NOT NULL DEFAULT ''",
                'deadline': "TEXT NOT NULL DEFAULT ''",
                'notice_revision': 'INTEGER NOT NULL DEFAULT 0',
                'notice_alerted': 'INTEGER NOT NULL DEFAULT 0',
                'notice_unread': 'INTEGER NOT NULL DEFAULT 0',
                'activity_notes': "TEXT NOT NULL DEFAULT ''",
                'intent': "TEXT NOT NULL DEFAULT '未决定'",
                'checklist': "TEXT NOT NULL DEFAULT '[]'",
                'reminder_mode': "TEXT NOT NULL DEFAULT 'inherit'",
                'task_minutes': "TEXT NOT NULL DEFAULT '[60,30]'",
                'remind_again_at': 'REAL',
                'link_identity': "TEXT NOT NULL DEFAULT ''",
            }.items():
                if name not in columns:
                    db.execute(f'ALTER TABLE tasks ADD COLUMN {name} {definition}')
            db.execute('''CREATE TABLE IF NOT EXISTS notices(
                id TEXT PRIMARY KEY, url TEXT NOT NULL, source TEXT NOT NULL,
                captured REAL NOT NULL, body TEXT NOT NULL)''')
            db.execute(
                'CREATE TABLE IF NOT EXISTS alerts(id TEXT PRIMARY KEY, title TEXT NOT NULL, body TEXT NOT NULL, created REAL NOT NULL, is_read INTEGER NOT NULL DEFAULT 0)'
            )
            db.execute('CREATE TABLE IF NOT EXISTS reminder_keys(id TEXT PRIMARY KEY)')
            db.execute(
                'CREATE TABLE IF NOT EXISTS attachments(id TEXT PRIMARY KEY,image BLOB NOT NULL,source TEXT NOT NULL,message_id TEXT NOT NULL,created REAL NOT NULL,is_qr INTEGER NOT NULL)'
            )
            db.execute(
                'CREATE TABLE IF NOT EXISTS task_attachments(url TEXT NOT NULL,image_id TEXT NOT NULL,PRIMARY KEY(url,image_id))'
            )
            db.execute('CREATE INDEX IF NOT EXISTS notices_by_url ON notices(url, captured DESC)')
            # Keep every old URL and its metadata. Only populate an additive lookup.
            pending = db.execute("SELECT url FROM tasks WHERE link_identity=''").fetchall()
            db.executemany(
                'UPDATE tasks SET link_identity=? WHERE url=?',
                [(task_identity(r['url']) or '', r['url']) for r in pending],
            )
            db.execute('CREATE INDEX IF NOT EXISTS tasks_by_link_identity ON tasks(link_identity,created)')

    def revision_token(self):
        """Invalidate views after local commits, external writes, or file replacement."""
        with self.lock:
            stamps = []
            for name in (self.path, self.path + '-wal'):
                try:
                    stat = Path(name).stat()
                    stamps.append((stat.st_ino, stat.st_size, stat.st_mtime_ns, stat.st_ctime_ns))
                except FileNotFoundError:
                    stamps.append(None)
            return self._revision, tuple(stamps)

    def recover_inflight(self):
        """Call only at exclusive application startup, never for an inspection connection."""
        with self.connection() as db:
            db.execute(
                "UPDATE tasks SET state='pending', note='上次打开过程被中断，已恢复待处理', updated=? WHERE state='opening'",
                (time.time(),),
            )

    @contextmanager
    def connection(self, timeout=10):
        with self.lock:
            db = sqlite3.connect(self.path, timeout=timeout)
            db.row_factory = sqlite3.Row
            try:
                with db:
                    yield db
                if db.total_changes:
                    self._revision += 1
            finally:
                db.close()

    def preview_additions(self, urls):
        """Read-only estimate using the same identity rules as capture.

        Counts may change if another capture commits after this snapshot.
        Historical duplicates remain intact, including their exact URL matches.
        """
        total = new = 0
        seen = set()
        with self.connection() as db:
            db.execute('BEGIN')
            for raw in urls:
                url = canonical_url(raw)
                if not url:
                    raise ValueError('不支持的活动文档链接')
                total += 1
                identity = task_identity(url)
                if self._matching_task(db, url, identity) is None and identity not in seen:
                    new += 1
                    seen.add(identity)
        return {'links': total, 'new': new, 'merged': total - new}

    def add_many(self, urls, source='手动添加', notices=None):
        now, count = time.time(), 0
        with self.connection() as db:
            db.execute('BEGIN IMMEDIATE')
            notices = list(notices or [])

            def resolved(raw):
                url = canonical_url(raw)
                if not url:
                    return None, None
                identity = task_identity(url)
                row = self._matching_task(db, url, identity)
                return (row['url'] if row else url), identity

            candidates = {resolved(raw)[0] for raw, _ in notices}
            existing = {
                url for url in candidates if url and db.execute('SELECT 1 FROM tasks WHERE url=?', (url,)).fetchone()
            }
            for raw in urls:
                url, identity = resolved(raw)
                if not url:
                    raise ValueError('不支持的活动文档链接')
                count += db.execute(
                    'INSERT OR IGNORE INTO tasks(url,source,created,updated,link_identity) VALUES(?,?,?,?,?)',
                    (url, source, now, now, identity),
                ).rowcount
            for raw, body in notices or []:
                url, _ = resolved(raw)
                if not url or not db.execute('SELECT 1 FROM tasks WHERE url=?', (url,)).fetchone():
                    continue
                captured = isinstance(body, ChatMessage)
                identity = ('chat-content\0' + body.content) if captured else body
                key = hashlib.sha256((url + '\0' + source + '\0' + identity).encode()).hexdigest()
                old = db.execute('SELECT 1 FROM notices WHERE url=? LIMIT 1', (url,)).fetchone()
                same = db.execute(
                    'SELECT 1 FROM notices WHERE url=? AND (id=? OR body=?) LIMIT 1', (url, key, body)
                ).fetchone()
                if captured and not db.execute('SELECT 1 FROM notices WHERE id=?', (key,)).fetchone():
                    # Adopt the stable identity without changing the original
                    # raw text, timestamp, source or any task state/counters.
                    previous = db.execute('SELECT id,body,source FROM notices WHERE url=?', (url,)).fetchall()
                    for notice in previous:
                        prefix = url + '\0' + notice['source'] + '\0'
                        if notice['id'] == hashlib.sha256((prefix + identity).encode()).hexdigest():
                            same = True
                            continue
                        legacy = hashlib.sha256((prefix + notice['body']).encode()).hexdigest()
                        if notice['id'] == legacy and body.matches_legacy(notice['body']):
                            same = True
                            if notice['source'] == source:
                                db.execute('UPDATE notices SET id=? WHERE id=?', (key, notice['id']))
                                break
                db.execute('INSERT OR IGNORE INTO notices VALUES(?,?,?,?,?)', (key, url, source, now, body))
                if url in existing and old and not same:
                    db.execute('UPDATE tasks SET notice_revision=notice_revision+1,notice_unread=1 WHERE url=?', (url,))
        return count

    def read_notice(self, url):
        with self.connection() as db:
            db.execute('UPDATE tasks SET notice_unread=0 WHERE url=?', (url,))

    def save_activity(self, url, notes, intent, checklist, mode, minutes):
        if len(notes) > 20000 or intent not in ('未决定', '想参加', '待考虑', '不参加', '已参加'):
            raise ValueError('备注过长或参加意向无效。')
        if len(checklist) > 100 or any(
            not isinstance(x, dict)
            or not isinstance(x.get('text'), str)
            or not x['text'].strip()
            or len(x['text']) > 300
            or not isinstance(x.get('done'), bool)
            for x in checklist
        ):
            raise ValueError('准备清单最多100项，每项1—300字。')
        if mode not in ('inherit', 'custom', 'off'):
            raise ValueError('请选择提醒方式。')
        from reminders import parse_minutes

        minutes = parse_minutes(minutes)
        if mode == 'custom' and not minutes:
            raise ValueError('自定义提醒至少需要一个提前量。')
        with self.connection() as db:
            db.execute(
                'UPDATE tasks SET activity_notes=?,intent=?,checklist=?,reminder_mode=?,task_minutes=?,updated=? WHERE url=?',
                (notes, intent, json.dumps(checklist, ensure_ascii=False), mode, json.dumps(minutes), time.time(), url),
            )
            if mode == 'off':
                db.execute('UPDATE tasks SET remind_again_at=NULL WHERE url=?', (url,))

    def remind_later(self, url, minutes=10):
        with self.connection() as db:
            db.execute(
                "UPDATE tasks SET remind_again_at=? WHERE url=? AND state!='done' AND disposition='active'",
                (time.time() + minutes * 60, url),
            )

    def alert(self, key, title, body):
        with self.connection() as db:
            return bool(
                db.execute(
                    'INSERT OR IGNORE INTO alerts(id,title,body,created) VALUES(?,?,?,?)',
                    (key, title, body, time.time()),
                ).rowcount
            )

    def alerts(self):
        with self.connection() as db:
            return [dict(r) for r in db.execute('SELECT * FROM alerts ORDER BY created DESC LIMIT 300')]

    def read_alerts(self):
        with self.connection() as db:
            db.execute('UPDATE alerts SET is_read=1 WHERE is_read=0')

    def unread_alert_count(self):
        with self.connection() as db:
            return db.execute('SELECT COUNT(*) FROM alerts WHERE is_read=0').fetchone()[0]

    @staticmethod
    def _matching_task(db, url, identity):
        row = db.execute('SELECT * FROM tasks WHERE url=?', (url,)).fetchone()
        if row is None and identity:
            row = db.execute(
                'SELECT * FROM tasks WHERE link_identity=? ORDER BY created,url LIMIT 1', (identity,)
            ).fetchone()
        return row

    def task(self, url):
        with self.connection() as db:
            row = self._matching_task(db, url, task_identity(url))
            return dict(row) if row else None

    def notices(self, url):
        with self.connection() as db:
            return [dict(r) for r in db.execute('SELECT * FROM notices WHERE url=? ORDER BY captured DESC', (url,))]

    def save_dates(self, url, start, end, deadline):
        start, end, deadline = validate_dates(start, end, deadline)
        with self.connection() as db:
            db.execute(
                'UPDATE tasks SET event_start=?,event_end=?,deadline=?,updated=? WHERE url=?',
                (start, end, deadline, time.time(), url),
            )

    def matching_urls(self, query):
        # Parameters keep punctuation, %, and _ literal, including in notice text.
        with self.connection() as db:
            return query_matching_urls(db, query)

    def wake_snoozed(self, now=None):
        now = time.time() if now is None else now
        with self.connection() as db:
            return wake_due_tasks(db, now)

    def disposition(self, urls, mode, until=None):
        if mode not in ('active', 'ignored', 'snoozed'):
            raise ValueError('未知任务操作')
        if mode == 'snoozed' and (until is None or until <= time.time()):
            raise ValueError('暂缓时间必须晚于现在')
        changed = 0
        with self.connection() as db:
            for url in urls:
                changed += db.execute(
                    "UPDATE tasks SET disposition=?,snooze_until=?,updated=? WHERE url=? AND state NOT IN ('opening','done')",
                    (mode, until if mode == 'snoozed' else None, time.time(), url),
                ).rowcount
        return changed

    def actionable(self, url):
        task = self.task(url)
        return bool(task and task['disposition'] == 'active' and task['state'] != 'done')

    def report(self, url, report, title=''):
        with self.connection() as db:
            db.execute(
                "UPDATE tasks SET field_report=?,title=CASE WHEN ?!='' THEN ? ELSE title END,updated=? WHERE url=? AND state='opened' AND disposition='active'",
                (json.dumps(report, ensure_ascii=False), title, title, time.time(), url),
            )

    def rows(self, limit=None):
        with self.connection() as db:
            sql = "SELECT * FROM tasks ORDER BY CASE disposition WHEN 'ignored' THEN 2 WHEN 'snoozed' THEN 1 ELSE 0 END, CASE state WHEN 'opening' THEN 0 WHEN 'pending' THEN 1 WHEN 'failed' THEN 2 WHEN 'opened' THEN 3 ELSE 4 END, created DESC"
            return [dict(r) for r in db.execute(sql + (' LIMIT ?' if limit else ''), (limit,) if limit else ())]

    def counts(self):
        with self.connection() as db:
            return dict(db.execute('SELECT state,COUNT(*) FROM tasks GROUP BY state').fetchall())

    def state(self, url):
        with self.connection() as db:
            row = db.execute('SELECT state FROM tasks WHERE url=?', (url,)).fetchone()
            return row['state'] if row else None

    def due(self, now=None, eligibility=None):
        now = time.time() if now is None else now
        with self.connection() as db:
            return has_due_tasks(db, now, eligibility)

    def claim(self, now=None, eligibility=None):
        now = time.time() if now is None else now
        with self.connection() as db:
            db.execute('BEGIN IMMEDIATE')
            clause, args = eligibility or ('1', ())
            row = db.execute(
                "SELECT * FROM tasks WHERE disposition='active' AND (state='pending' OR (state='failed' AND retry_at IS NOT NULL AND retry_at<=?)) AND ("
                + clause
                + ") ORDER BY created LIMIT 1",
                (now, *args),
            ).fetchone()
            if not row:
                return None
            db.execute(
                "UPDATE tasks SET state='opening',attempts=attempts+1,updated=?,retry_at=NULL,note='正在打开网页' WHERE url=?",
                (now, row['url']),
            )
            return dict(db.execute('SELECT * FROM tasks WHERE url=?', (row['url'],)).fetchone())

    def opened(self, url, note='网页已打开，等待你核对并手动提交'):
        with self.connection() as db:
            db.execute(
                "UPDATE tasks SET state='opened',retry_at=NULL,note=?,updated=? WHERE url=? AND state='opening'",
                (note, time.time(), url),
            )

    def note(self, url, note):
        with self.connection() as db:
            db.execute("UPDATE tasks SET note=?,updated=? WHERE url=? AND state='opened'", (note, time.time(), url))

    def failed(self, url, reason, max_attempts=3, delay=30, now=None):
        now = time.time() if now is None else now
        with self.connection() as db:
            row = db.execute('SELECT attempts FROM tasks WHERE url=?', (url,)).fetchone()
            if row:
                retry_at = now + delay * (2 ** max(0, row['attempts'] - 1)) if row['attempts'] < max_attempts else None
                db.execute(
                    "UPDATE tasks SET state='failed',retry_at=?,note=?,updated=? WHERE url=? AND state='opening'",
                    (retry_at, reason[:500], now, url),
                )

    def release(self, url):
        with self.connection() as db:
            db.execute(
                "UPDATE tasks SET state='pending',note='打开被中断，已返回待处理',updated=? WHERE url=? AND state='opening'",
                (time.time(), url),
            )

    def requeue(self, urls):
        changed = 0
        with self.connection() as db:
            for url in urls:
                changed += db.execute(
                    "UPDATE tasks SET state='pending',disposition='active',snooze_until=NULL,attempts=0,retry_at=NULL,note='手动重新排队',updated=? WHERE url=? AND state!='opening'",
                    (time.time(), url),
                ).rowcount
        return changed

    def done(self, urls):
        with self.connection() as db:
            for url in urls:
                db.execute(
                    "UPDATE tasks SET state='done',disposition='active',snooze_until=NULL,retry_at=NULL,note='用户标记已处理（不是自动确认提交）',updated=? WHERE url=? AND state!='opening'",
                    (time.time(), url),
                )


def state_label(row):
    if row.get('disposition') == 'ignored':
        return '已忽略'
    if row.get('disposition') == 'snoozed':
        return '已暂缓'
    if row['state'] == 'failed':
        return '等待重试' if row['retry_at'] is not None else '需要手动重试'
    return {'pending': '待处理', 'opening': '打开中', 'opened': '已打开·待确认', 'done': '已处理'}[row['state']]
