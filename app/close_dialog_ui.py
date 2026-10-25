"""Close-choice presentation; tray and shutdown behavior remain in App."""

import tkinter as tk
from tkinter import ttk

from appearance import themed
from design_tokens import dp
from ui_layout import work_area, centered_position


def show_close_dialog(app):
    win = tk.Toplevel(app.root)
    win.withdraw()  # Finish layout and placement before first display.
    app.close_window = win
    win.title('关闭方式')
    win.transient(app.root)
    win.resizable(False, False)
    bounds = work_area(app.root)
    margin = dp(win, 16)
    width = min(dp(win, 520), bounds[2] - bounds[0] - margin * 2)
    padding = dp(win, 24)
    body = ttk.Frame(win, padding=padding)
    body.pack(fill='both', expand=True)
    wrap = max(1, width - padding * 2)
    ttk.Label(body, text='关闭后如何运行？', style='Dialog.TLabel').pack(anchor='w')
    for title, description in (
        ('最小化到托盘', '助手继续监测和处理，可从右下角托盘恢复。聊天客户端需保持在目标群；锁屏或休眠时可能无法读取。'),
        ('彻底退出', '停止监测并关闭助手浏览器。退出前请处理未提交的表单。'),
    ):
        ttk.Label(body, text=title, style='Strong.TLabel').pack(anchor='w', pady=(dp(win, 16), dp(win, 4)))
        ttk.Label(body, text=description, style='Muted.TLabel', justify='left', wraplength=wrap).pack(
            anchor='w', fill='x'
        )
    buttons = ttk.Frame(body)
    buttons.pack(fill='x', pady=(dp(win, 24), 0))

    def hide():
        win.destroy()
        app.hide_to_tray()

    def quit_now():
        win.destroy()
        app.shutdown()

    cancel = ttk.Button(buttons, text='取消', command=win.destroy)
    cancel.pack(side='right')
    ttk.Button(buttons, text='彻底退出', command=quit_now).pack(side='right', padx=dp(win, 8))
    ttk.Button(buttons, text='最小化到托盘', style='Primary.TButton', command=hide).pack(side='left')
    win.bind('<Escape>', lambda event: win.destroy())
    win.protocol('WM_DELETE_WINDOW', win.destroy)
    themed(app, win)
    win.update_idletasks()
    height = win.winfo_reqheight()
    parent = app.root
    parent_rect = (parent.winfo_rootx(), parent.winfo_rooty(), parent.winfo_width(), parent.winfo_height())
    x, y = centered_position(parent_rect, (width, height), bounds, margin)
    # '+-N' specifies an absolute negative origin, rather than distance from the right edge.
    win.geometry(f'{width}x{height}+{x}+{y}')
    win.deiconify()
    win.lift()
    cancel.focus_set()
    return win
