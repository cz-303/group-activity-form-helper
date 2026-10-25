"""卸载 群活动填表助手.

Started from the install folder (Start menu / "已安装的应用"), it first copies itself to %TEMP% and restarts
from there with ``--target <folder>`` so the installed copy of this file can be removed.
``--quiet --target <folder> [--delete-data]`` is used by the automated release tests only.
"""

import json
import os
import queue
import shutil
import subprocess
import sys
import tempfile
import threading
from pathlib import Path

import install_common as ic
from data_purge import PURGE_SUMMARY
from install_ui import auto_wrap, check, make_root, resource_path


def _report(argv, payload, code):
    text = json.dumps(payload, ensure_ascii=False)
    if '--result' in argv:
        Path(argv[argv.index('--result') + 1]).write_text(text, encoding='utf-8')
    print(text)
    return code


def run_quiet(argv):
    target = Path(argv[argv.index('--target') + 1])
    try:
        result = ic.uninstall(target, delete_data='--delete-data' in argv)
    except ic.InstallError as error:
        return _report(argv, {'ok': False, 'error': str(error)}, 2)
    _report(argv, {'ok': True, **result}, 0)
    return 0 if not result['failed'] and not result['data_remaining'] else 1


def locate_target():
    here = Path(sys.executable if getattr(sys, 'frozen', False) else __file__).resolve().parent
    if ic.read_manifest(here):
        return here
    return ic.installed_location()


def relaunch_from_temp(target):
    copy = Path(tempfile.gettempdir()) / f'gfhelper-uninstall-{os.getpid()}.exe'
    shutil.copy2(sys.executable, copy)
    subprocess.Popen([str(copy), '--target', str(target)], cwd=tempfile.gettempdir(), close_fds=True)


class UninstallWindow:
    def __init__(self, target):
        import tkinter as tk
        from tkinter import ttk

        self.target = Path(target)
        self.events = queue.Queue()
        manifest = ic.read_manifest(self.target) or {}
        self.root = make_root(f'卸载 {ic.DISPLAY_NAME}', 540, 360, str(resource_path('activity-helper.ico')))
        body = ttk.Frame(self.root, padding=(24, 20))
        body.pack(fill='both', expand=True)
        ttk.Label(
            body, text=f'卸载 {ic.DISPLAY_NAME} {manifest.get("version", "")}'.strip(), style='Title.TLabel'
        ).pack(anchor='w')
        auto_wrap(
            ttk.Label(
                body,
                text=f'将删除程序文件、开始菜单和桌面快捷方式，以及“已安装的应用”中的登记。\n位置：{self.target}',
                justify='left',
            )
        ).pack(fill='x', pady=(10, 12))
        self.delete_var = tk.BooleanVar(value=False)
        self.delete_check = check(body, text='同时删除我的数据', variable=self.delete_var, command=self.toggle_warning)
        self.delete_check.pack(anchor='w')
        self.keep_note = auto_wrap(
            ttk.Label(
                body,
                text='不勾选时保留 data 文件夹（任务、资料、设置和登录状态），重新安装到同一位置会继续使用。',
                style='Muted.TLabel',
                justify='left',
            )
        )
        self.keep_note.pack(fill='x', pady=(2, 0))
        self.warning = auto_wrap(
            ttk.Label(
                body,
                text=f'将永久删除：{PURGE_SUMMARY}。自动备份也在其中。删除后无法恢复；已提交到网页的内容不受影响。',
                style='Warn.TLabel',
                justify='left',
            )
        )
        self.progress = ttk.Progressbar(body, maximum=1.0)
        self.progress.pack(fill='x', pady=(16, 4))
        self.status = tk.StringVar(value='卸载前请在助手中选择“彻底退出”。')
        auto_wrap(ttk.Label(body, textvariable=self.status, style='Muted.TLabel', justify='left')).pack(fill='x')
        buttons = ttk.Frame(body)
        buttons.pack(side='bottom', fill='x')
        self.cancel = ttk.Button(buttons, text='取消', command=self.root.destroy)
        self.cancel.pack(side='right')
        self.action = ttk.Button(buttons, text='卸载', style='Primary.TButton', command=self.start)
        self.action.pack(side='right', padx=(0, 8))
        self.root.protocol('WM_DELETE_WINDOW', self.close)

    def close(self):
        if str(self.cancel.cget('state')) != 'disabled':
            self.root.destroy()

    def toggle_warning(self):
        if self.delete_var.get():
            self.keep_note.pack_forget()
            self.warning.pack(anchor='w', fill='x', pady=(6, 0), before=self.progress)
        else:
            self.warning.pack_forget()
            self.keep_note.pack(fill='x', pady=(2, 0), before=self.progress)

    def start(self):
        from tkinter import messagebox

        delete_data = self.delete_var.get()
        if delete_data and not messagebox.askyesno(
            '确认删除数据',
            '确定要同时删除全部助手数据吗？此操作不可撤销。',
            icon='warning',
            default='no',
            parent=self.root,
        ):
            return
        for widget in (self.delete_check, self.action, self.cancel):
            widget.configure(state='disabled')

        def work():
            try:
                result = ic.uninstall(
                    self.target,
                    delete_data=delete_data,
                    progress=lambda fraction, text: self.events.put(('progress', fraction, text)),
                )
                self.events.put(('done', result))
            except ic.InstallError as error:
                self.events.put(('error', str(error)))
            except Exception as error:
                self.events.put(('error', f'卸载未完成：{type(error).__name__}: {error}'))

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
                    self.status.set('卸载未完成，未报告为成功。')
                    for widget in (self.delete_check, self.action, self.cancel):
                        widget.configure(state='normal')
                    messagebox.showerror('卸载未完成', event[1], parent=self.root)
                    return
                elif event[0] == 'done':
                    self.finish(event[1])
                    return
        except queue.Empty:
            pass
        self.root.after(50, self.poll)

    def finish(self, result):
        from tkinter import messagebox

        lines = ['已卸载 ' + ic.DISPLAY_NAME + '。']
        if result['data_kept']:
            lines.append(
                f'你的数据保留在：{self.target / "data"}\n重新安装到同一位置会继续使用；不再需要时可手动删除该文件夹。'
            )
        if result['data_remaining']:
            lines.append(
                '部分数据被占用未能删除：'
                + '、'.join(result['data_remaining'][:6])
                + f'\n请稍后手动删除 {self.target / "data"}'
            )
        if result['failed']:
            lines.append('以下项目未能删除，请稍后手动删除：\n' + '\n'.join(result['failed'][:6]))
        ok = not result['data_remaining'] and not result['failed']
        (messagebox.showinfo if ok else messagebox.showwarning)(
            '卸载完成' if ok else '卸载基本完成', '\n\n'.join(lines), parent=self.root
        )
        self.root.destroy()

    def run(self):
        self.root.mainloop()


def main(argv):
    if '--quiet' in argv:
        return run_quiet(argv)
    if '--target' in argv:
        UninstallWindow(argv[argv.index('--target') + 1]).run()
        return 0
    target = locate_target()
    if not target or not ic.read_manifest(target):
        from tkinter import Tk, messagebox

        root = Tk()
        root.withdraw()
        messagebox.showerror('无法卸载', '没有找到 ' + ic.DISPLAY_NAME + ' 的安装记录，未删除任何文件。')
        root.destroy()
        return 1
    if getattr(sys, 'frozen', False):
        relaunch_from_temp(target)
        return 0
    UninstallWindow(target).run()
    return 0


if __name__ == '__main__':
    sys.exit(main(sys.argv))
