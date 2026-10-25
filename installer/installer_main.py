"""群活动填表助手 安装程序 (per-user, no administrator rights).

GUI by default. ``--quiet --target <folder> [--no-desktop]`` is used by the automated release tests only.
"""

import json
import queue
import subprocess
import sys
import threading
from pathlib import Path

import install_common as ic
from install_ui import auto_wrap, check, make_root, resource_path

VERSION = '1.1'


def payload_path():
    return resource_path('payload.zip')


def _report(argv, payload, code):
    text = json.dumps(payload, ensure_ascii=False)
    if '--result' in argv:
        Path(argv[argv.index('--result') + 1]).write_text(text, encoding='utf-8')
    print(text)
    return code


def run_quiet(argv):
    target = Path(argv[argv.index('--target') + 1])
    try:
        result = ic.install(payload_path(), target, VERSION, desktop_shortcut='--no-desktop' not in argv)
    except ic.InstallError as error:
        return _report(argv, {'ok': False, 'error': str(error)}, 2)
    _report(argv, {'ok': True, **result}, 0)
    return 0


class InstallerWindow:
    def __init__(self):
        import tkinter as tk
        from tkinter import ttk

        self.tk, self.ttk = tk, ttk
        self.root = make_root(f'安装 {ic.DISPLAY_NAME} {VERSION}', 560, 400, str(resource_path('activity-helper.ico')))
        self.events = queue.Queue()
        self.installed_target = None
        existing = ic.installed_location()
        manifest = ic.read_manifest(existing) if existing else None
        default = existing if manifest else ic.default_install_dir()

        body = ttk.Frame(self.root, padding=(24, 20))
        body.pack(fill='both', expand=True)
        ttk.Label(body, text=f'{ic.DISPLAY_NAME} {VERSION}', style='Title.TLabel').pack(anchor='w')
        ttk.Label(body, text='整理群活动通知，确认后辅助填写报名表 · 非官方工具', style='Muted.TLabel').pack(
            anchor='w', pady=(2, 14)
        )
        if manifest:
            auto_wrap(
                ttk.Label(
                    body,
                    text=f'已安装 {manifest.get("version", "旧版本")}，将升级到 {VERSION}。你的数据会保留。'
                    '请先在助手中“彻底退出”。',
                    style='Info.TLabel',
                    justify='left',
                )
            ).pack(fill='x', pady=(0, 12))
        ttk.Label(body, text='安装位置').pack(anchor='w')
        row = ttk.Frame(body)
        row.pack(fill='x', pady=(4, 10))
        self.target_var = tk.StringVar(value=str(default))
        self.entry = ttk.Entry(row, textvariable=self.target_var)
        self.entry.pack(side='left', fill='x', expand=True)
        self.browse = ttk.Button(row, text='浏览…', command=self.choose)
        self.browse.pack(side='left', padx=(8, 0))
        self.desktop_var = tk.BooleanVar(value=bool((ic.WinRegistry().read() or {}).get('DesktopShortcut', 1)))
        self.launch_var = tk.BooleanVar(value=True)
        self.desktop_check = check(body, text='创建桌面快捷方式', variable=self.desktop_var)
        self.desktop_check.pack(anchor='w')
        self.launch_check = check(body, text='安装完成后启动', variable=self.launch_var)
        self.launch_check.pack(anchor='w', pady=(2, 10))
        auto_wrap(
            ttk.Label(
                body,
                text='安装到当前 Windows 用户，无需管理员权限。数据保存在安装目录的 data 文件夹，'
                '升级和默认卸载都会保留；需要时可在卸载时勾选删除，或在助手“更多 → 清除全部数据并退出”。',
                style='Muted.TLabel',
                justify='left',
            )
        ).pack(anchor='w', fill='x')
        self.progress = ttk.Progressbar(body, maximum=1.0)
        self.progress.pack(fill='x', pady=(14, 4))
        self.status = tk.StringVar(value='准备就绪')
        auto_wrap(ttk.Label(body, textvariable=self.status, style='Muted.TLabel', justify='left')).pack(fill='x')
        buttons = ttk.Frame(body)
        buttons.pack(side='bottom', fill='x')
        self.cancel = ttk.Button(buttons, text='取消', command=self.root.destroy)
        self.cancel.pack(side='right')
        self.action = ttk.Button(buttons, text='安装', style='Primary.TButton', command=self.start)
        self.action.pack(side='right', padx=(0, 8))
        self.root.bind('<Return>', lambda event: self.action.invoke())
        self.root.protocol('WM_DELETE_WINDOW', self.close)

    def close(self):
        if str(self.cancel.cget('state')) != 'disabled':
            self.root.destroy()

    def choose(self):
        from tkinter import filedialog

        chosen = filedialog.askdirectory(parent=self.root, title='选择安装位置', mustexist=False)
        if chosen:
            folder = Path(chosen)
            if folder.name != ic.APP_FOLDER:
                folder = folder / ic.APP_FOLDER
            self.target_var.set(str(folder))

    def set_busy(self, busy):
        state = 'disabled' if busy else 'normal'
        for widget in (self.entry, self.browse, self.desktop_check, self.launch_check, self.action, self.cancel):
            widget.configure(state=state)

    def start(self):
        target = Path(self.target_var.get().strip().strip('"'))
        self.set_busy(True)
        desktop = self.desktop_var.get()

        def work():
            try:
                result = ic.install(
                    payload_path(),
                    target,
                    VERSION,
                    desktop_shortcut=desktop,
                    progress=lambda fraction, text: self.events.put(('progress', fraction, text)),
                )
                self.events.put(('done', result))
            except ic.InstallError as error:
                self.events.put(('error', str(error)))
            except Exception as error:  # unexpected: show it, never claim success
                self.events.put(('error', f'安装未完成：{type(error).__name__}: {error}'))

        threading.Thread(target=work, daemon=True).start()
        self.root.after(50, self.poll)

    def poll(self):
        from tkinter import messagebox

        try:
            while True:
                event = self.events.get_nowait()
                if event[0] == 'progress':
                    self.progress.configure(value=event[1])
                    self.status.set(event[2])
                elif event[0] == 'error':
                    self.progress.configure(value=0)
                    self.status.set('安装未完成。')
                    self.set_busy(False)
                    messagebox.showerror('安装未完成', event[1], parent=self.root)
                    return
                elif event[0] == 'done':
                    self.installed_target = Path(event[1]['target'])
                    self.status.set(f'安装完成：{self.installed_target}')
                    self.progress.configure(value=1.0)
                    self.action.configure(text='完成', state='normal', command=self.finish)
                    self.launch_check.configure(state='normal')
                    return
        except queue.Empty:
            pass
        self.root.after(50, self.poll)

    def finish(self):
        if self.launch_var.get() and self.installed_target:
            exe = self.installed_target / ic.EXE_NAME
            subprocess.Popen([str(exe)], cwd=str(self.installed_target), close_fds=True)
        self.root.destroy()

    def run(self):
        self.root.mainloop()


def main(argv):
    if '--quiet' in argv:
        return run_quiet(argv)
    InstallerWindow().run()
    return 0


if __name__ == '__main__':
    sys.exit(main(sys.argv))
