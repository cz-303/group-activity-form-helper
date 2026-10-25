"""Read the target group from the desktop chat client chosen in Settings.

The client is identified only by the program file the user selected
(config "client_path"); nothing here hard-codes a product name.
"""

import ctypes
from ctypes import wintypes
from pathlib import Path
from chat_reader import read_group_windows
from diagnostics import UserFacingError


def client_process(config):
    """Lower-case file name of the selected client program, or '' when none is set."""
    name = Path(str(config.get('client_path') or '')).name.lower()
    return name if name.endswith('.exe') else ''


def process_path(pid):
    kernel = ctypes.WinDLL('kernel32', use_last_error=True)
    kernel.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    kernel.OpenProcess.restype = wintypes.HANDLE
    kernel.QueryFullProcessImageNameW.argtypes = [
        wintypes.HANDLE,
        wintypes.DWORD,
        wintypes.LPWSTR,
        ctypes.POINTER(wintypes.DWORD),
    ]
    kernel.CloseHandle.argtypes = [wintypes.HANDLE]
    handle = kernel.OpenProcess(0x1000, False, pid)
    if not handle:
        return ''
    try:
        buf, size = ctypes.create_unicode_buffer(32768), wintypes.DWORD(32768)
        return buf.value if kernel.QueryFullProcessImageNameW(handle, 0, buf, ctypes.byref(size)) else ''
    finally:
        kernel.CloseHandle(handle)


def client_windows(auto, process_name):
    if not process_name:
        raise UserFacingError('请先在设置 → 群聊监测中选择聊天客户端程序。')
    return [w for w in auto.GetRootControl().GetChildren() if Path(process_path(w.ProcessId)).name.lower() == process_name]


def read_group(auto, group, process_name, capture_images=None):
    windows = client_windows(auto, process_name)
    return read_group_windows(windows, group, ctypes.windll.user32.IsIconic, capture_images)
