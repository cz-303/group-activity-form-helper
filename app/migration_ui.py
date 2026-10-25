from ui_layout import fit_window
from design_tokens import FONTS
from storage_io import atomic_bytes
from diagnostics import describe_error
import json, sqlite3, tempfile, shutil
from pathlib import Path
import tkinter as tk
from tkinter import ttk, filedialog, messagebox
from backup_support import make_backup, stage_restore, inspect_backup
from task_store import TaskStore
from appearance import themed

from file_operations import snapshot_old_folder, prepare_old_folder, export_migration


def show_migration(app):
    win = tk.Toplevel(app.root)
    win.transient(app.root)
    win.title('升级与数据迁移')
    fit_window(win, 750, 470)
    f = ttk.Frame(win, padding=16)
    f.pack(fill='both', expand=True)
    ttk.Label(f, text='把活动记录带到新版', font=FONTS['title']).pack(anchor='w', pady=(0, 14))
    ttk.Label(
        f,
        text='迁移任务、通知原文、备注、清单、提醒和个人设置。\n不打包浏览器登录状态，新版可能需要重新登录腾讯文档。\n请先退出旧版助手，避免两个版本同时运行。',
        wraplength=690,
    ).pack(anchor='w')

    def export():
        if not app.can_start_file_job():
            return
        target = filedialog.asksaveasfilename(
            parent=win,
            title='导出迁移包',
            defaultextension='.zip',
            initialfile='活动助手迁移包.zip',
            filetypes=[('迁移包', '*.zip')],
        )
        if not target:
            return
        config = app.get_config()
        app.run_file_job(
            '导出迁移包',
            lambda: export_migration(app.store, app.data, config, target),
            lambda _: app.status.set('迁移包已导出，包含个人资料，请妥善保管。', state='success'),
        )

    def old_folder():
        if not app.can_start_file_job():
            return
        folder = filedialog.askdirectory(parent=win, title='选择旧版程序目录或data目录')
        if not folder:
            return
        old = Path(folder)
        data = old if (old / 'tasks.sqlite3').is_file() else old / 'data'
        if data.resolve() == app.data.resolve():
            messagebox.showinfo('请选择旧版', '这是当前版本正在使用的数据目录，无需导入。', parent=win)
            return
        app.run_file_job(
            '读取旧版数据',
            lambda: prepare_old_folder(folder),
            app.confirm_restore,
            lambda prepared: prepared.discard_unless_retained(),
        )

    for label, command in [
        ('导出当前数据为迁移包', export),
        ('导入迁移包 / 备份包', app.choose_restore),
        ('直接读取旧版文件夹', old_folder),
    ]:
        ttk.Button(f, text=label, command=command).pack(fill='x', pady=(14, 0))
    ttk.Label(
        f,
        text='迁移是替换，不是合并；恢复后的任务处理默认暂停。\n本功能负责数据迁移，不会联网下载或替换软件程序。',
        wraplength=690,
    ).pack(anchor='w', pady=18)
    themed(app, win)
