"""Date editor presentation; the store continues to validate and save dates."""

import tkinter as tk
from tkinter import ttk, messagebox
from appearance import themed
from design_tokens import dp
from detail_header_ui import SummaryLabel
from ui_layout import fit_window, center_window, bind_wrap
from diagnostics import describe_error
from activity_time import time_references


def show_dates_dialog(app, url, row):
    win = tk.Toplevel(app.root)
    win.withdraw()
    app.dates_window = win
    win.title('活动时间与报名截止')
    win.transient(app.root)
    fit_window(win, 620, 540, (480, 400))
    # Reserve the action row before the expanding form.
    bar = ttk.Frame(win, padding=(dp(win, 16), dp(win, 12)))
    bar.pack(side='bottom', fill='x')
    frame = ttk.Frame(win, padding=dp(win, 16))
    frame.pack(fill='both', expand=True)
    frame.columnconfigure(1, weight=1)
    title = tk.StringVar(win, value=row['title'] or '活动收集表')
    SummaryLabel(frame, title, style='Section.TLabel').grid(
        row=0, column=0, columnspan=2, sticky='ew', pady=(0, dp(win, 16))
    )
    variables = []
    first_entry = None
    for index, (key, label) in enumerate(
        (('event_start', '活动开始'), ('event_end', '活动结束（可空）'), ('deadline', '报名截止')), 1
    ):
        ttk.Label(frame, text=label).grid(row=index, column=0, sticky='w', padx=(0, dp(win, 16)), pady=dp(win, 8))
        variable = tk.StringVar(win, value=row[key])
        variables.append(variable)
        entry = ttk.Entry(frame, textvariable=variable)
        entry.grid(row=index, column=1, sticky='ew', pady=dp(win, 8))
        if first_entry is None:
            first_entry = entry
    for index, text in (
        (4, '格式：2026-09-25 15:00 或 2026-09-25；留空可清除。\n按本机当地时间；只填截止日期表示当天结束。'),
        (5, '通知时间参考：请核对年份、日期和报名截止。'),
    ):
        label = ttk.Label(frame, text=text, style='Muted.TLabel', justify='left', wraplength=dp(win, 550))
        label.grid(row=index, column=0, columnspan=2, sticky='ew', pady=dp(win, 8))
        bind_wrap(frame, label, lambda event: event.width - dp(win, 32))
    ref_frame = ttk.Frame(frame)
    ref_frame.grid(row=6, column=0, columnspan=2, sticky='nsew')
    frame.rowconfigure(6, weight=1)
    scroll = ttk.Scrollbar(ref_frame, orient='vertical')
    scroll.pack(side='right', fill='y')
    reference = tk.Text(ref_frame, wrap='word', height=6, width=40, yscrollcommand=scroll.set)
    reference.pack(side='left', fill='both', expand=True)
    scroll.configure(command=reference.yview)
    reference.insert('1.0', time_references(app.store.notices(url)) or '未找到时间相关文字。')
    reference.configure(state='disabled')

    def save():
        try:
            app.store.save_dates(url, *(variable.get() for variable in variables))
            app.emit('活动时间和报名截止已保存。时间筛选与排序已更新。', state='success')
            app.refresh_table()
            win.destroy()
        except Exception as error:
            messagebox.showerror('无法保存时间', describe_error(error), parent=win)

    ttk.Button(bar, text='保存时间', style='Primary.TButton', command=save).pack(side='right')
    ttk.Button(bar, text='取消', command=win.destroy).pack(side='right', padx=dp(win, 8))
    win.bind('<Escape>', lambda event: win.destroy())
    themed(app, win)
    center_window(win, app.root)
    win.deiconify()
    first_entry.focus_set()
    return win
