"""Shared visual vocabulary. Font sizes are points; spacing is logical pixels."""

FONT_FAMILY = 'Microsoft YaHei UI'
FONTS = {
    'body': (FONT_FAMILY, 10),
    'caption': (FONT_FAMILY, 9),
    'section': (FONT_FAMILY, 12, 'bold'),
    'dialog': (FONT_FAMILY, 14, 'bold'),
    'title': (FONT_FAMILY, 17, 'bold'),
    'strong': (FONT_FAMILY, 10, 'bold'),
}
SPACE = (4, 8, 12, 16, 24)
CONTROL_RADIUS = 6
CARD_RADIUS = 8
CONTROL_PADDING = (12, 6)
# 1.1 look: Windows 11 style. "bg" is the window/shell colour; ordinary widgets
# sit on white "surface" cards. "nav_*" are the sidebar states on the shell.
PALETTES = {
    'light': dict(
        bg='#F3F3F3',
        sidebar='#F3F3F3',
        surface='#FFFFFF',
        fg='#1A1A1A',
        muted='#5E5E5E',
        border='#E5E5E5',
        strong_border='#D1D1D1',
        accent='#005FB8',
        hover='#F5F5F5',
        selected='#E6EEF8',
        nav_hover='#EAEAEA',
        nav_selected='#FFFFFF',
        primary_fg='#FFFFFF',
        primary_hover='#1A6FC2',
        info='#005FB8',
        info_bg='#E6EEF8',
        success='#0F7B0F',
        success_bg='#DFF6DD',
        warning='#8A5300',
        warning_bg='#FFF4CE',
        error='#C42B1C',
        error_bg='#FDE7E9',
        neutral_bg='#F0F0F0',
        danger_hover='#A82418',
    ),
    'dark': dict(
        bg='#202020',
        sidebar='#202020',
        surface='#2B2B2B',
        fg='#FFFFFF',
        muted='#C5C5C5',
        border='#3B3B3B',
        strong_border='#4A4A4A',
        accent='#60CDFF',
        hover='#323232',
        selected='#1F3A4D',
        nav_hover='#2D2D2D',
        nav_selected='#2B2B2B',
        primary_fg='#000000',
        primary_hover='#4CC2FF',
        info='#99D5FF',
        info_bg='#1F3448',
        success='#6CCB5F',
        success_bg='#1E3320',
        warning='#FCE100',
        warning_bg='#433519',
        error='#FF99A4',
        error_bg='#442726',
        neutral_bg='#333333',
        danger_hover='#FFB3BB',
    ),
}
# Opening a form does not mean registration succeeded. Color always has a label.
STATES = {
    'idle': ('就绪', 'muted', 'neutral_bg'),
    'empty': ('暂无内容', 'muted', 'neutral_bg'),
    'info': ('提示', 'info', 'info_bg'),
    'running': ('进行中', 'info', 'info_bg'),
    'success': ('已完成', 'success', 'success_bg'),
    'warning': ('需留意', 'warning', 'warning_bg'),
    'error': ('未完成', 'error', 'error_bg'),
    'cancelled': ('已取消', 'muted', 'neutral_bg'),
}


def scale_factor(widget):
    return max(0.75, min(4.0, float(widget.tk.call('tk', 'scaling')) / (96 / 72)))


def dp(widget, value):
    return round(value * scale_factor(widget))
