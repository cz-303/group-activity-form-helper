from ui_layout import fit_window
from design_tokens import FONTS
from ui_layout import settings_tab
import tkinter as tk
from tkinter import ttk, messagebox, filedialog
import time
from appearance import themed


def show_alerts(app):
    if getattr(app, 'alerts_window', None) and app.alerts_window.winfo_exists():
        app.alerts_window.lift()
        return
    win = tk.Toplevel(app.root)
    app.alerts_window = win
    win.title('提醒中心')
    fit_window(win, 760, 540)
    frame = ttk.Frame(win, padding=16)
    frame.pack(fill='both', expand=True)
    text = tk.Text(frame, wrap='word', font=FONTS['body'], padx=12, pady=12)
    text.pack(fill='both', expand=True)

    def refresh():
        rows = app.store.alerts()
        text.configure(state='normal')
        text.delete('1.0', 'end')
        text.insert(
            '1.0',
            '\n\n────────\n\n'.join(
                f"{'未读 · ' if not r['is_read'] else ''}{r['title']}\n{time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(r['created']))}\n{r['body']}"
                for r in rows
            )
            or '暂无提醒。',
        )
        text.configure(state='disabled')

    def read():
        app.store.read_alerts()
        refresh()

    bar = ttk.Frame(frame)
    bar.pack(fill='x', pady=(12, 0))
    ttk.Button(bar, text='刷新', command=refresh).pack(side='left')
    ttk.Button(bar, text='全部标记已读', command=read).pack(side='right')
    refresh()
    themed(app, win)


def alert_popup(app, items):
    config = app.get_config()
    if config.get('notify_sound', False):
        app.root.bell()
    if not config.get('notify_popup', True):
        return
    # One window aggregates alerts. Durable details remain in the reminder centre.
    if not getattr(app, 'alert_window', None) or not app.alert_window.winfo_exists():
        win = tk.Toplevel(app.root)
        app.alert_window = win
        app.alert_popup_count = 0
        win.title('活动助手提醒')
        win.attributes('-topmost', True)
        win.resizable(False, False)
        frame = ttk.Frame(win, padding=16)
        frame.pack(fill='both', expand=True)
        app.alert_popup_text = tk.StringVar()
        ttk.Label(frame, textvariable=app.alert_popup_text, wraplength=340).pack(anchor='w')

        def view():
            app.root.deiconify()
            app.root.lift()
            show_alerts(app)
            win.destroy()

        ttk.Button(frame, text='查看提醒', command=view).pack(anchor='e', pady=(14, 0))
        win.geometry(f'390x180+{max(0, win.winfo_screenwidth() - 420)}+{max(0, win.winfo_screenheight() - 250)}')
    app.alert_popup_count += len(items)
    app.alert_popup_text.set(f"{items[-1][0]}\n共 {app.alert_popup_count} 条提醒，详情已保存到提醒中心。")
    if getattr(app, 'alert_popup_timer', None):
        app.root.after_cancel(app.alert_popup_timer)

    def dismiss():
        if app.alert_window.winfo_exists():
            app.alert_window.destroy()

    app.alert_popup_timer = app.root.after(15000, dismiss)
    themed(app, app.alert_window)


def extra_settings(app, notebook, config, variables, parent=None):
    frame = settings_tab(notebook, '外观与备份', 20, parent=parent)
    frame.columnconfigure(1, weight=1)
    ttk.Label(frame, text='界面主题').grid(row=0, column=0, sticky='w', pady=12)
    v = tk.StringVar(value='深色' if config.get('theme') == 'dark' else '浅色')
    variables['theme'] = v
    chooser = ttk.Combobox(frame, textvariable=v, values=['浅色', '深色'], state='readonly')
    chooser.grid(row=0, column=1, sticky='ew', padx=15)
    for row, key, label, default in [(1, 'backup_enabled', '自动备份任务和设置', True)]:
        v = tk.BooleanVar(value=config.get(key, default))
        variables[key] = v
        ttk.Checkbutton(frame, text=label, variable=v).grid(row=row, column=0, columnspan=2, sticky='w', pady=16)
    for row, key, label, default in [
        (2, 'backup_hours', '自动备份间隔（小时，1—168）', 24),
        (3, 'backup_keep', '保留最近自动备份（份，2—100）', 10),
    ]:
        ttk.Label(frame, text=label).grid(row=row, column=0, sticky='w', pady=12)
        v = tk.StringVar(value=str(config.get(key, default)))
        variables[key] = v
        ttk.Entry(frame, textvariable=v).grid(row=row, column=1, sticky='ew', padx=15)
    ttk.Label(
        frame,
        text='备份保存在 data/backups，包含个人资料，不包含浏览器登录状态。\n首次运行会建立备份；之后按间隔检查，软件关闭时不会备份。\n手动备份和恢复前副本不会被自动清理。',
        wraplength=690,
    ).grid(row=4, column=0, columnspan=2, sticky='w', pady=16)
    bar = ttk.Frame(frame)
    bar.grid(row=5, column=0, columnspan=2, sticky='w', pady=8)
    ttk.Button(bar, text='立即备份', command=lambda: app.start_backup(False)).pack(side='left')
    ttk.Button(bar, text='从备份恢复…', command=app.choose_restore).pack(side='left', padx=12)
    ttk.Label(
        frame,
        text='恢复会替换任务和设置；校验后安排下次启动生效。\n恢复前会保留当前副本，恢复后不会自动打开表单。',
        wraplength=690,
    ).grid(row=6, column=0, columnspan=2, sticky='w', pady=12)
    ttk.Label(frame, text='© 2026 Chen Zhuo\nDesigned by Chen Zhuo', font=FONTS['body']).grid(
        row=7, column=0, columnspan=2, sticky='w', pady=20
    )
