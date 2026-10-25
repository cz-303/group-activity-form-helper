"""Validate imported SQLite contents before migrations or installation."""

import json
import math
import sqlite3
from activity_time import validate_dates


def validate_database(path):
    db = sqlite3.connect(path)
    db.row_factory = sqlite3.Row
    try:
        db.execute('PRAGMA trusted_schema=OFF')
        if db.execute('PRAGMA quick_check').fetchone()[0] != 'ok':
            raise ValueError('任务数据库损坏。')
        objects = db.execute("SELECT type,name,sql FROM sqlite_master WHERE name NOT LIKE 'sqlite_%'").fetchall()
        allowed = {'tasks', 'notices', 'alerts', 'reminder_keys', 'attachments', 'task_attachments'}

        def supported_object(row):
            if row['type'] == 'index':
                # Only our plain column index; never accept imported expressions or triggers.
                sql = ' '.join(row['sql'].lower().split())
                return (
                    row['name'] == 'notices_by_url'
                    and sql == 'create index notices_by_url on notices(url, captured desc)'
                ) or (
                    row['name'] == 'tasks_by_link_identity'
                    and sql == 'create index tasks_by_link_identity on tasks(link_identity,created)'
                )
            return row['type'] == 'table' and row['name'] in allowed and 'VIRTUAL' not in row['sql'].upper()

        if any(not supported_object(r) for r in objects):
            raise ValueError('备份包含不支持的数据库对象。')
        required = {'url', 'source', 'created', 'updated', 'state', 'attempts', 'retry_at', 'note'}
        columns = {r['name'] for r in db.execute('PRAGMA table_info(tasks)')}
        if not required <= columns:
            raise ValueError('任务数据库结构不符。')
    finally:
        db.close()
    # Upgrade only the disposable copy, not the user's active database.
    from task_store import TaskStore
    from task_links import canonical_task_url as canonical_url
    from task_links import task_identity
    from reminders import parse_minutes

    store = TaskStore(path)
    count = 0
    with store.connection() as db:
        for record in db.execute('SELECT * FROM tasks'):
            row = dict(record)
            count += 1
            if count > 100000:
                raise ValueError('备份任务超过10万项，无法直接导入。')
            text_fields = (
                'url',
                'source',
                'note',
                'title',
                'field_report',
                'event_start',
                'event_end',
                'deadline',
                'activity_notes',
                'checklist',
                'task_minutes',
            )
            if any(not isinstance(row[k], str) or len(row[k]) > 500000 for k in text_fields):
                raise ValueError('备份中的任务文字类型或长度不正确。')
            if canonical_url(row['url']) != row['url'] or row['state'] not in (
                'pending',
                'opening',
                'opened',
                'failed',
                'done',
            ):
                raise ValueError('备份中存在不支持的任务链接或状态。')
            if row['link_identity'] != task_identity(row['url']):
                raise ValueError('备份中的文档识别信息无效。')
            if row['disposition'] not in ('active', 'snoozed', 'ignored'):
                raise ValueError('备份中存在无效的任务处理方式。')
            for key in ('created', 'updated', 'retry_at', 'snooze_until', 'remind_again_at'):
                value = row[key]
                if value is None and key not in ('created', 'updated'):
                    continue
                if not isinstance(value, (int, float)) or not math.isfinite(value) or not 0 <= value <= 253402214400:
                    raise ValueError('备份中的时间戳无效。')
            for key in ('attempts', 'notice_revision', 'notice_alerted', 'notice_unread'):
                if not isinstance(row[key], int) or row[key] < 0:
                    raise ValueError('备份中的任务计数无效。')
            validate_dates(row['event_start'], row['event_end'], row['deadline'])
            checklist = json.loads(row['checklist'])
            if (
                not isinstance(checklist, list)
                or len(checklist) > 100
                or any(
                    not isinstance(x, dict)
                    or not isinstance(x.get('text'), str)
                    or not x['text'].strip()
                    or len(x['text']) > 300
                    or type(x.get('done')) is not bool
                    for x in checklist
                )
            ):
                raise ValueError('备份中的准备清单格式错误。')
            if row['intent'] not in ('未决定', '想参加', '待考虑', '不参加', '已参加') or row['reminder_mode'] not in (
                'inherit',
                'custom',
                'off',
            ):
                raise ValueError('备份中的参加意向或提醒方式无效。')
            minutes = json.loads(row['task_minutes'])
            if not isinstance(minutes, list) or any(type(x) is not int for x in minutes):
                raise ValueError('备份中的提醒提前量无效。')
            if row['reminder_mode'] == 'custom' and not minutes:
                raise ValueError('专属提醒缺少提前量。')
            parse_minutes(minutes)
            if row['field_report']:
                report = json.loads(row['field_report'])
                if not isinstance(report, dict):
                    raise ValueError('备份中的字段提示格式错误。')
                for key in ('missing', 'review', 'blocked'):
                    if not isinstance(report.get(key), list) or any(not isinstance(x, str) for x in report[key]):
                        raise ValueError('备份中的字段提示内容错误。')
                if any(type(report.get(key)) is not int or report[key] < 0 for key in ('filled', 'questions')):
                    raise ValueError('备份中的字段计数错误。')
        for row in db.execute('SELECT id,url,source,captured,body FROM notices'):
            if (
                any(not isinstance(row[k], str) for k in ('id', 'url', 'source', 'body'))
                or not isinstance(row['captured'], (float, int))
                or not math.isfinite(row['captured'])
            ):
                raise ValueError('备份中的通知格式错误。')
        for row in db.execute('SELECT id,title,body,created,is_read FROM alerts'):
            if (
                any(not isinstance(row[k], str) for k in ('id', 'title', 'body'))
                or row['is_read'] not in (0, 1)
                or not isinstance(row['created'], (int, float))
                or not math.isfinite(row['created'])
            ):
                raise ValueError('备份中的提醒格式错误。')
        for row in db.execute('SELECT id FROM reminder_keys'):
            if not isinstance(row['id'], str):
                raise ValueError('备份中的提醒记录格式错误。')
        from attachment_store import MAX_TOTAL_BYTES, MAX_IMAGE_BYTES, MAX_IMAGES, image_bytes
        import hashlib

        total = 0
        if db.execute('SELECT COUNT(*) FROM attachments').fetchone()[0] > MAX_IMAGES:
            raise ValueError('备份图片数量超过1000张。')
        for row in db.execute('SELECT * FROM attachments'):
            raw = row['image']
            if not isinstance(raw, bytes) or len(raw) > MAX_IMAGE_BYTES:
                raise ValueError('备份图片格式或大小无效。')
            total += len(raw)
            if total > MAX_TOTAL_BYTES:
                raise ValueError('备份图片总量超过80MB。')
            if hashlib.sha256(raw).hexdigest() != row['id']:
                raise ValueError('备份图片校验失败。')
            image_bytes(raw)
            if (
                any(not isinstance(row[k], str) for k in ('source', 'message_id'))
                or len(row['source']) > 4000
                or len(row['message_id']) > 200
                or row['is_qr'] not in (0, 1)
                or not isinstance(row['created'], (int, float))
                or not math.isfinite(row['created'])
                or not 0 <= row['created'] <= 253402214400
            ):
                raise ValueError('备份图片来源信息无效。')
        for row in db.execute('SELECT url,image_id FROM task_attachments'):
            if (
                not db.execute('SELECT 1 FROM tasks WHERE url=?', (row['url'],)).fetchone()
                or not db.execute('SELECT 1 FROM attachments WHERE id=?', (row['image_id'],)).fetchone()
            ):
                raise ValueError('备份图片关联信息无效。')
    return count
