"""Configuration validation and storage; no Tk or platform automation dependencies."""

from copy import deepcopy
import json
from core import DEFAULTS, validate_custom_fields
from storage_io import atomic_text

SETTINGS = dict(
    DEFAULTS,
    poll_seconds=3,
    max_tabs=8,
    max_attempts=3,
    retry_seconds=30,
    auto_resume=False,
    notify_new=True,
    notify_popup=True,
    notify_sound=False,
    theme='light',
    notify_updates=True,
    monitor_alerts=True,
    monitor_alert_seconds=30,
    deadline_reminders=True,
    deadline_minutes=[60, 30],
    backup_enabled=True,
    backup_hours=24,
    backup_keep=10,
    capture_qr=False,
    auto_open_forms=True,
    auto_open_documents=True,
)
BOOL_KEYS = (
    'auto_resume',
    'notify_new',
    'notify_popup',
    'notify_sound',
    'notify_updates',
    'monitor_alerts',
    'deadline_reminders',
    'backup_enabled',
    'capture_qr',
    'auto_open_forms',
    'auto_open_documents',
)


def validate_settings(values):
    if not isinstance(values, dict):
        raise ValueError('设置必须是有效的配置对象。')
    result = deepcopy(SETTINGS)
    result.update({key: value for key, value in values.items() if key in SETTINGS})
    result['custom_fields'] = validate_custom_fields(values.get('custom_fields', {}))
    for key in DEFAULTS:
        value = result[key]
        if not isinstance(value, str) or len(value) > 4000 or '\x00' in value:
            raise ValueError('资料和监测设置必须是最多4000字的文字，不能包含空字符。')
        result[key] = value.strip()
    if not any(x.strip() for x in result['keywords'].replace('，', ',').split(',')):
        raise ValueError('通知关键词不能为空。')
    for key in BOOL_KEYS:
        if type(result[key]) is not bool:
            raise ValueError('开关设置必须为开启或关闭，不能用文字代替。')
    ranges = [
        ('poll_seconds', 2, 60, '检查间隔'),
        ('max_tabs', 1, 12, '同时打开表单数'),
        ('max_attempts', 1, 10, '最大尝试次数'),
        ('retry_seconds', 5, 600, '重试间隔'),
        ('monitor_alert_seconds', 10, 600, '异常提醒等待时间'),
        ('backup_hours', 1, 168, '备份间隔'),
        ('backup_keep', 2, 100, '备份保留数量'),
    ]
    for key, low, high, label in ranges:
        value = result[key]
        if isinstance(value, bool) or not isinstance(value, (str, int)):
            raise ValueError(label + '必须是整数。')
        try:
            result[key] = int(value)
        except ValueError:
            raise ValueError(label + '必须是整数。') from None
        if not low <= result[key] <= high:
            raise ValueError(f'{label}应在 {low} 至 {high} 之间。')
    levels = result['deadline_minutes']
    if isinstance(levels, str):
        levels = [x.strip() for x in levels.replace('，', ',').split(',') if x.strip()]
    if not isinstance(levels, list) or any(isinstance(x, bool) or not isinstance(x, (str, int)) for x in levels):
        raise ValueError('提醒提前量请填写分钟数，例如60,30。')
    try:
        levels = sorted({int(x) for x in levels}, reverse=True)
    except ValueError:
        raise ValueError('提醒提前量请填写整数分钟数。') from None
    if len(levels) > 10 or any(not 1 <= x <= 10080 for x in levels):
        raise ValueError('最多10个提前量，每个为1—10080分钟。')
    if result['deadline_reminders'] and not levels:
        raise ValueError('开启截止提醒时，请至少设置一个提前量。')
    result['deadline_minutes'] = levels
    theme = result['theme']
    if not isinstance(theme, str):
        raise ValueError('请选择浅色或深色主题。')
    result['theme'] = {'浅色': 'light', '深色': 'dark'}.get(theme, theme)
    if result['theme'] not in ('light', 'dark'):
        raise ValueError('请选择浅色或深色主题。')
    return result


def load_settings(path):
    try:
        if path.stat().st_size > 1024 * 1024:
            raise ValueError('配置过大')
        return validate_settings(json.loads(path.read_text(encoding='utf-8'))), ''
    except FileNotFoundError:
        return validate_settings({}), ''
    except (OSError, ValueError, TypeError):
        return validate_settings(
            {}
        ), '原设置无法读取或格式不正确，已暂停任务处理并使用空白资料；原文件未覆盖。请检查设置或恢复备份。'


def save_settings(path, values):
    config = validate_settings(values)
    with atomic_text(path) as stream:
        json.dump(config, stream, ensure_ascii=False, indent=2)
    return config
