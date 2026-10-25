"""Bounded header presentation with explicit access to the unchanged full text."""

import tkinter as tk
from tkinter import ttk, font as tkfont

from appearance import themed
from design_tokens import dp, FONTS
from ui_layout import ResizeUpdate, fit_window


def ellipsize(text, measure, width):
    """Normalize only the display copy and keep the result within available pixels."""
    display = ' '.join(text.split())
    if not display or width <= 0:
        return ''
    if measure(display) <= width:
        return display
    if measure('…') > width:
        return ''
    low, high = 0, len(display)
    while low < high:
        middle = (low + high + 1) // 2
        if measure(display[:middle] + '…') <= width:
            low = middle
        else:
            high = middle - 1
    return display[:low] + '…'


class SummaryLabel(ttk.Label):
    def __init__(self, parent, source, **kwargs):
        self.display = tk.StringVar(parent)
        super().__init__(parent, textvariable=self.display, wraplength=0, **kwargs)
        self.source = source
        self.cached_key = None
        self.font_key = None
        self.measured_font = None
        self.job = ResizeUpdate(self, self.update_summary)
        self.bind('<Configure>', self.job.request, add='+')
        self.bind('<Destroy>', self.close_source, add='+')
        self.source_trace = source.trace_add('write', lambda *_: self.job.request(None))
        self.job.request(None)

    def update_summary(self, event):
        font_spec = self.cget('font') or ttk.Style(self).lookup(self.cget('style') or 'TLabel', 'font')
        font_key = (str(font_spec), float(self.tk.call('tk', 'scaling')))
        if font_key != self.font_key:
            self.measured_font = tkfont.Font(root=self, font=font_spec)
            self.font_key = font_key
        width = max(0, self.winfo_width() - dp(self, 8))
        key = (self.source.get(), width, font_key)
        if key == self.cached_key:
            return
        self.cached_key = key
        text = ellipsize(key[0], self.measured_font.measure, width)
        if self.display.get() != text:
            self.display.set(text)

    def close_source(self, event):
        if event.widget is self:
            self.source.trace_remove('write', self.source_trace)


def set_header(app, title, meta, ready=False):
    app.detail_title.set(title)
    app.detail_meta.set(meta)
    selected = tuple(app.table.selection())
    app.full_detail = (selected, title, meta) if ready and len(selected) == 1 else None
    if app.full_detail:
        app.info_button.configure(state='normal')
        if not app.info_button.winfo_manager():
            app.info_button.pack(side='left', padx=8, before=app.preview_button)
    else:
        app.info_button.configure(state='disabled')
        app.info_button.pack_forget()


def show_full_detail(app):
    snapshot = getattr(app, 'full_detail', None)
    if not snapshot or snapshot[0] != tuple(app.table.selection()):
        app.status.set('请选中一个任务，等待详情读取完成后查看完整信息。', state='info')
        return
    window = getattr(app, 'full_detail_window', None)
    if window is not None and window.winfo_exists():
        window.destroy()
    window = tk.Toplevel(app.root)
    app.full_detail_window = window
    window.title('完整任务信息 · 只读快照')
    window.transient(app.root)
    fit_window(window, 720, 520)
    frame = ttk.Frame(window, padding=dp(window, 16))
    frame.pack(fill='both', expand=True)
    ttk.Label(
        frame,
        text='以下是打开时的完整信息，可选中复制；通知原文仍保留在任务详情中。',
        wraplength=dp(window, 650),
        style='Muted.TLabel',
    ).pack(anchor='w', pady=(0, dp(window, 8)))
    body = ttk.Frame(frame)
    body.pack(fill='both', expand=True)
    text = tk.Text(body, wrap='word', font=FONTS['body'], width=40, height=10)
    scroll = ttk.Scrollbar(body, orient='vertical', command=text.yview)
    text.configure(yscrollcommand=scroll.set)
    text.pack(side='left', fill='both', expand=True)
    scroll.pack(side='right', fill='y')
    text.insert('1.0', '活动标题\n' + snapshot[1] + '\n\n状态与来源\n' + snapshot[2])
    text.configure(state='disabled')
    ttk.Button(frame, text='关闭', command=window.destroy).pack(anchor='e', pady=(dp(window, 12), 0))
    themed(app, window)
