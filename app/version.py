"""Single source for the product name and version shown in window titles."""

APP_NAME = '群活动填表助手'
APP_VERSION = '1.1'
ISSUES_URL = 'https://github.com/cz-303/group-activity-form-helper/issues'


def window_title(suffix='非官方工具'):
    return f'{APP_NAME} v{APP_VERSION} · {suffix}'
