"""Delete all local user data after the assistant has fully exited.

Used by the in-app "清除全部数据并退出" command (via a detached ``--purge-data <pid>`` helper process) and by the
uninstaller. Only a directory named ``data`` that sits directly in the application folder and looks like this
application's data folder is ever removed; nothing outside it is touched.
"""

import os
import shutil
import stat
import subprocess
import sys
import time
from pathlib import Path

PURGE_FLAG = '--purge-data'
APP_EXE_NAME = '群活动填表助手.exe'
DATA_MARKERS = (
    'tasks.sqlite3',
    'config.json',
    'browser',
    'backups',
    'diagnostics.log',
    'restore_pending.zip',
    'restore_journal.json',
)
PURGE_SUMMARY = '任务清单与通知原文、个人资料与自定义字段、设置、活动图片、自动备份、运行记录，以及助手浏览器的登录状态'


def is_app_data_dir(path, app_root):
    """True only for <app_root>/data of an installed or source copy of this application."""
    try:
        data = Path(path).resolve()
        root = Path(app_root).resolve()
    except OSError:
        return False
    if data.name.lower() != 'data' or data.parent != root or not data.is_dir():
        return False
    if not ((root / APP_EXE_NAME).is_file() or (root / 'app.py').is_file()):
        return False
    names = [entry.name for entry in data.iterdir()]
    return not names or any(name in DATA_MARKERS for name in names)


def _make_writable_and_retry(function, path, _error):
    os.chmod(path, stat.S_IWRITE)
    function(path)


def purge_data_dir(path, app_root, timeout=60.0, sleep=time.sleep, clock=time.monotonic):
    """Remove everything inside the data folder, then the folder itself.

    Locked files (for example a browser that is still closing) are retried until ``timeout``.
    Returns the sorted names that could not be removed; an empty list means the data is gone.
    """
    data = Path(path)
    if not data.exists():
        return []
    if not is_app_data_dir(data, app_root):
        raise ValueError('拒绝删除：所选位置不是本程序的数据文件夹。')
    data = data.resolve()
    deadline = clock() + timeout
    while True:
        remaining = []
        for child in list(data.iterdir()):
            try:
                if child.is_dir() and not child.is_symlink():
                    shutil.rmtree(child, onexc=_make_writable_and_retry)
                else:
                    os.chmod(child, stat.S_IWRITE)
                    child.unlink()
            except OSError:
                remaining.append(child.name)
        if not remaining:
            try:
                data.rmdir()
            except OSError:
                pass
            return []
        if clock() >= deadline:
            return sorted(remaining)
        sleep(1)


def wait_for_process_exit(pid, timeout=90.0):
    """Wait for the requesting assistant process to exit. Returns True if it is gone."""
    if os.name != 'nt' or not pid:
        return True
    import ctypes

    kernel = ctypes.WinDLL('kernel32', use_last_error=True)
    kernel.OpenProcess.restype = ctypes.c_void_p
    kernel.OpenProcess.argtypes = [ctypes.c_uint32, ctypes.c_int, ctypes.c_uint32]
    kernel.WaitForSingleObject.argtypes = [ctypes.c_void_p, ctypes.c_uint32]
    kernel.CloseHandle.argtypes = [ctypes.c_void_p]
    synchronize = 0x00100000
    handle = kernel.OpenProcess(synchronize, 0, int(pid))
    if not handle:
        return True
    try:
        return kernel.WaitForSingleObject(handle, int(timeout * 1000)) == 0
    finally:
        kernel.CloseHandle(handle)


def helper_command(app_root, pid):
    if getattr(sys, 'frozen', False):
        return [sys.executable, PURGE_FLAG, str(pid)]
    return [sys.executable, str(Path(app_root) / 'app.py'), PURGE_FLAG, str(pid)]


def launch_purge_helper(app_root, pid=None):
    """Start the detached helper that deletes data once this process has exited."""
    flags = 0x00000008 | 0x00000200 if os.name == 'nt' else 0  # DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP
    return subprocess.Popen(
        helper_command(app_root, pid or os.getpid()), cwd=str(app_root), creationflags=flags, close_fds=True
    )


def run_purge_helper(argv, app_root, data_dir, quiet=None):
    """Entry point for ``app.py --purge-data <pid>``. Returns a process exit code."""
    pid = int(argv[2]) if len(argv) > 2 and argv[2].isdigit() else None
    wait_for_process_exit(pid)
    error = None
    try:
        remaining = purge_data_dir(data_dir, app_root)
    except Exception as exc:  # report, never guess success
        remaining, error = None, str(exc)
    if quiet is None:
        quiet = os.environ.get('GFHELPER_PURGE_QUIET') == '1'
    if remaining == []:
        title, text, ok = '数据已清除', f'已删除本机保存的全部助手数据。\n位置：{data_dir}', True
    elif error:
        title, text, ok = '未能清除数据', error, False
    else:
        title, ok = '部分数据未能删除', False
        text = (
            '以下项目可能仍被占用（例如浏览器尚未完全关闭），未能删除：\n'
            + '、'.join(remaining[:8])
            + f'\n\n请稍后手动删除文件夹：\n{data_dir}'
        )
    if not quiet:
        import tkinter as tk
        from tkinter import messagebox

        root = tk.Tk()
        root.withdraw()
        (messagebox.showinfo if ok else messagebox.showwarning)(title, text, parent=root)
        root.destroy()
    return 0 if ok else 1


def request_purge(app):
    """Two confirmations, then a normal safe shutdown; the helper deletes data after exit."""
    from tkinter import messagebox

    data_dir = app.data
    first = messagebox.askyesno(
        '清除全部数据',
        f'将删除本机保存的全部助手数据：\n{PURGE_SUMMARY}。\n\n'
        '删除后无法恢复。自动备份也保存在数据文件夹中，会一并删除；如需保留，'
        '请先在“设置”中点击“立即备份”，再把备份文件复制到其他文件夹。\n\n'
        f'数据文件夹：{data_dir}\n\n助手会先安全退出，再删除数据。是否继续？',
        icon='warning',
        default='no',
        parent=app.root,
    )
    if not first:
        return False
    if not messagebox.askyesno(
        '再次确认', '确定要删除全部数据吗？此操作不可撤销。', icon='warning', default='no', parent=app.root
    ):
        return False
    app.purge_requested = True
    app.shutdown()
    return True
