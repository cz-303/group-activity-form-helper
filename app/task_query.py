"""Read-only task snapshots and latest-request scheduling; no Tk dependencies."""

from dataclasses import dataclass
from pathlib import Path
import json
import sqlite3
import threading
import time
from background_jobs import BackgroundJobs
from task_store import query_matching_urls, state_label
from task_details import build_detail, format_notice
from task_links import kind_label
from activity_time import filter_time, sort_tasks
from field_report import report_summary
from reminders import countdown


@dataclass(frozen=True)
class ViewRequest:
    search: str
    state: str
    source: str
    time_filter: str
    sort: str
    selection: tuple
    minute: int


class QueryCancelled(Exception):
    pass


class LatestQuery:
    """One active read and one replaceable pending request, using BackgroundJobs."""

    def __init__(self, reader, success, failure):
        self.reader, self.success, self.failure = reader, success, failure
        self.jobs = BackgroundJobs()
        self.latest = self.pending = None
        self.cancel = threading.Event()

    @property
    def busy(self):
        return self.jobs.busy or self.pending is not None

    def request(self, request):
        if self.jobs.closed:
            return
        if request != self.latest:
            self.latest = request
            self.cancel.set()
            self.pending = request
        elif not self.jobs.busy:
            self.pending = request
        self._start()

    def invalidate(self):
        # Used immediately on keystrokes, before the debounce timer fires.
        self.latest = self.pending = None
        self.cancel.set()

    def _start(self):
        if self.jobs.busy or self.pending is None or self.jobs.closed:
            return
        request = self.pending
        self.pending = None
        cancel = self.cancel = threading.Event()

        def completed(value):
            if not cancel.is_set() and request == self.latest:
                self.success(request, value)

        def failed(error):
            if not cancel.is_set() and request == self.latest and not isinstance(error, QueryCancelled):
                self.failure(request, error)

        self.jobs.start(lambda: self.reader(request, cancel), completed, failed)

    def poll(self):
        self.jobs.poll()
        self._start()

    def close(self):
        self.cancel.set()
        self.pending = None
        self.jobs.close()


class TaskViewReader:
    """Only the query worker owns this cache; no connection escapes a read call."""

    def __init__(self, database):
        self.database = Path(database)
        self.token = None
        self.rows = None
        self.search_key = self.matches = None
        self.detail_key = self.detail = None
        self.notice_key = self.notice_text = None
        self.result_key = self.result = None

    def _token(self):
        stamps = []
        for path in (self.database, Path(str(self.database) + '-wal')):
            try:
                stat = path.stat()
                stamps.append((stat.st_ino, stat.st_size, stat.st_mtime_ns, stat.st_ctime_ns))
            except FileNotFoundError:
                stamps.append(None)
        return tuple(stamps)

    def __call__(self, request, cancel):
        if cancel.is_set():
            raise QueryCancelled()
        token = self._token()
        key = (token, request)
        if key == self.result_key:
            return self.result
        # UI inputs are untrusted; reject abusive expressions before constructing SQL.
        if len(request.search) > 2000 or len(request.search.split()) > 32:
            raise ValueError('搜索内容请限制在2000字、32个关键词以内。')
        changed = token != self.token
        until = time.monotonic() + 5

        def interrupted():
            return cancel.is_set() or time.monotonic() > until

        db = sqlite3.connect(self.database.resolve().as_uri() + '?mode=ro', uri=True, timeout=0.2)
        db.row_factory = sqlite3.Row
        try:
            db.execute('PRAGMA query_only=ON')
            db.set_progress_handler(interrupted, 1000)
            db.execute('BEGIN')
            rows = [dict(r) for r in db.execute('SELECT * FROM tasks')] if changed or self.rows is None else self.rows
            matches = (
                query_matching_urls(db, request.search)
                if changed or self.search_key != request.search
                else self.matches
            )
            unread = db.execute('SELECT COUNT(*) FROM alerts WHERE is_read=0').fetchone()[0]
            selected = request.selection[0] if request.selection else None
            row = next((r for r in rows if r['url'] == selected), None)
            detail_key = (token, selected, len(request.selection), request.minute)
            notices = None
            image_count = 0
            if row and detail_key != self.detail_key:
                notices = [
                    dict(r) for r in db.execute('SELECT * FROM notices WHERE url=? ORDER BY captured DESC', (selected,))
                ]
                image_count = db.execute('SELECT COUNT(*) FROM task_attachments WHERE url=?', (selected,)).fetchone()[0]
        except sqlite3.OperationalError as error:
            if cancel.is_set():
                raise QueryCancelled() from error
            if time.monotonic() > until:
                raise TimeoutError('查询耗时较长，请减少关键词后重试。') from error
            raise
        finally:
            db.close()
        if cancel.is_set():
            raise QueryCancelled()

        def include(r):
            if matches is not None and r['url'] not in matches:
                return False
            if request.source != '全部来源' and r['source'] != request.source:
                return False
            if not filter_time(r, request.time_filter):
                return False
            choice = request.state
            if choice == '进行中':
                return r['disposition'] == 'active' and r['state'] != 'done'
            if choice == '已暂缓':
                return r['disposition'] == 'snoozed'
            if choice == '已忽略':
                return r['disposition'] == 'ignored'
            if choice == '已处理':
                return r['state'] == 'done'
            if choice == '通知有更新':
                return bool(r['notice_unread'])
            states = {'待处理': 'pending', '打开中': 'opening', '已打开': 'opened', '打开失败': 'failed'}
            return r['disposition'] == 'active' and r['state'] == states[choice] if choice in states else True

        matching = sort_tasks([r for r in rows if include(r)], request.sort)
        visible = matching[:500]
        values = []
        for r in visible:
            title = r['title'] or (kind_label(r['url']) + ' · ' + r['url'].split('?', 1)[0].rsplit('/', 1)[-1])
            report = json.loads(r['field_report']) if r['field_report'] else None
            values.append(
                (
                    r['url'],
                    (
                        ('[更新] ' if r['notice_unread'] else '') + title,
                        state_label(r),
                        report_summary(report),
                        countdown(r['deadline']),
                    ),
                )
            )
        if detail_key == self.detail_key:
            detail = self.detail
        elif row:
            notice_key = (row['notice_unread'], tuple((n['captured'], n['source'], n['body']) for n in notices))
            if notice_key != self.notice_key:
                self.notice_text = format_notice(notices, row['notice_unread'])
                self.notice_key = notice_key
            detail = build_detail(row, notices, len(request.selection), self.notice_text)
            detail['tabs']['处理信息'] += (
                f'\n\n活动图片：已关联 {image_count} 张。通过“任务操作 → 活动图片与群二维码”查看或管理。'
            )
        else:
            detail = None
        if cancel.is_set():
            raise QueryCancelled()
        result = {
            'rows': values,
            'total': len(rows),
            'pending': sum(r['state'] == 'pending' and r['disposition'] == 'active' for r in rows),
            'matched': len(matching),
            'sources': ['全部来源'] + sorted({r['source'] for r in rows if r['source'] != '全部来源'}),
            'detail': detail,
            'unread': unread,
            'key': key,
        }
        self.token, self.rows = token, rows
        self.search_key, self.matches = request.search, matches
        self.detail_key, self.detail = detail_key, detail
        self.result_key, self.result = key, result
        return result
