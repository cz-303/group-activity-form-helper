"""Local, user-confirmed activity dates; notice excerpts are only references."""

from datetime import datetime, timedelta
import re

TIME_FILTERS = ['全部时间', '24小时内截止', '7天内截止', '已截止', '未设截止', '今天有活动', '7天内有活动']
SORTS = ['最近捕获', '截止时间最近', '活动时间最近']


def normalize_date(value):
    value = value.strip()
    if not value:
        return ''
    for fmt in ('%Y-%m-%d %H:%M', '%Y-%m-%d'):
        try:
            return datetime.strptime(value, fmt).strftime(fmt)
        except ValueError:
            pass
    raise ValueError('请填写有效的 YYYY-MM-DD 或 YYYY-MM-DD HH:MM，例如 2026-09-25 15:00。')


def date_value(value, end=False):
    if not value:
        return None
    result = datetime.fromisoformat(value)
    if len(value) == 10 and end:
        result = result.replace(hour=23, minute=59, second=59, microsecond=999999)
    return result


def validate_dates(start, end, deadline):
    values = tuple(normalize_date(v) for v in (start, end, deadline))
    a, b, _ = values
    if b and not a:
        raise ValueError('填写活动结束时间前，请先填写开始时间。')
    if a and b and date_value(b, True) < date_value(a):
        raise ValueError('活动结束时间不能早于开始时间。')
    return values


def deadline_label(row, now=None):
    now = now or datetime.now()
    value = row.get('deadline', '')
    deadline = date_value(value, True)
    if not deadline:
        return '未设置'
    prefix = '已截止 · ' if deadline < now else ('24小时内 · ' if deadline <= now + timedelta(days=1) else '')
    return prefix + value


def filter_time(row, choice, now=None):
    now = now or datetime.now()
    deadline = date_value(row.get('deadline', ''), True)
    if choice == '已截止':
        return deadline is not None and deadline < now
    if choice == '未设截止':
        return deadline is None
    if choice in ('24小时内截止', '7天内截止'):
        return deadline is not None and now <= deadline <= now + timedelta(days=1 if choice == '24小时内截止' else 7)
    if choice in ('今天有活动', '7天内有活动'):
        start = date_value(row.get('event_start', ''))
        end = date_value(row.get('event_end', '') or row.get('event_start', ''), True)
        lower = now.replace(hour=0, minute=0, second=0, microsecond=0)
        upper = lower + timedelta(days=1 if choice == '今天有活动' else 7)
        return start is not None and start < upper and end >= lower
    return True


def sort_tasks(rows, choice):
    if choice == '截止时间最近':
        return sorted(rows, key=lambda r: (date_value(r.get('deadline', ''), True) or datetime.max, -r['created']))
    if choice == '活动时间最近':
        return sorted(rows, key=lambda r: (date_value(r.get('event_start', '')) or datetime.max, -r['created']))
    return sorted(rows, key=lambda r: -r['created'])


def time_references(notices):
    """Return exact notice lines, never infer years or relative dates."""
    lines = []
    for notice in notices:
        for line in notice['body'].splitlines():
            if re.search(
                r'截止|活动时间|报名时间|举行|举办|\d{1,2}月\d{1,2}[日号]|\d{4}[-/.]\d{1,2}[-/.]\d{1,2}|\d{1,2}[:：]\d{2}|今天|明天|本周|周[一二三四五六日天]',
                line,
            ):
                if line not in lines:
                    lines.append(line)
    return '\n\n'.join(lines)
