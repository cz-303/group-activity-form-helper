"""Install / uninstall logic for 群活动填表助手, shared by the installer and uninstaller executables.

Per-user install (no administrator rights): program files go to the chosen folder, user data stays in its
``data`` sub-folder and is never touched by install, upgrade or a normal uninstall. Every Windows-specific
side effect (registry, shortcuts, "is it running") goes through ``Environment`` so tests can redirect it.
"""

import base64
import hashlib
import json
import os
import shutil
import subprocess
import time
import zipfile
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath

APP_FOLDER = '群活动填表助手'
DISPLAY_NAME = '群活动填表助手'
PUBLISHER = 'Chen Zhuo'
CONTACT = 'https://github.com/cz-303/group-activity-form-helper/issues'
EXE_NAME = '群活动填表助手.exe'
UNINSTALLER_NAME = '卸载.exe'
MANIFEST_NAME = '.install-manifest.json'
UNINSTALL_KEY = r'Software\Microsoft\Windows\CurrentVersion\Uninstall\GroupActivityFormHelper'
PAYLOAD_PREFIX = 'app/'
STAGING_NAME = '.install-staging'
APP_SHORTCUT = '群活动填表助手.lnk'
UNINSTALL_SHORTCUT = '卸载 群活动填表助手.lnk'


class InstallError(Exception):
    """A user-facing reason the operation stopped. Nothing is reported as done when this is raised."""


# ---------------------------------------------------------------- Windows helpers


def _known_folder(csidl, fallback):
    if os.name == 'nt':
        import ctypes

        buffer = ctypes.create_unicode_buffer(260)
        if ctypes.windll.shell32.SHGetFolderPathW(None, csidl, None, 0, buffer) == 0 and buffer.value:
            return Path(buffer.value)
    return Path(fallback)


def default_install_dir():
    local = _known_folder(0x001C, os.environ.get('LOCALAPPDATA', Path.home() / 'AppData' / 'Local'))
    return local / 'Programs' / APP_FOLDER


def mutex_name(install_dir):
    """Same single-instance mutex name app.py derives from its data folder."""
    data = Path(install_dir) / 'data'
    try:
        data = data.resolve()
    except OSError:
        pass
    return 'Local\\GroupFormHelper_' + hashlib.sha256(str(data).lower().encode()).hexdigest()[:24]


def app_is_running(install_dir):
    if os.name != 'nt':
        return False
    import ctypes

    kernel = ctypes.WinDLL('kernel32', use_last_error=True)
    kernel.OpenMutexW.restype = ctypes.c_void_p
    kernel.OpenMutexW.argtypes = [ctypes.c_uint32, ctypes.c_int, ctypes.c_wchar_p]
    kernel.CloseHandle.argtypes = [ctypes.c_void_p]
    handle = kernel.OpenMutexW(0x00100000, 0, mutex_name(install_dir))
    if handle:
        kernel.CloseHandle(handle)
        return True
    return False


def _ps_quote(value):
    return "'" + str(value).replace("'", "''") + "'"


def create_shortcut(link, target, working_dir, icon, description):
    """Create a .lnk via the WScript.Shell COM object (PowerShell, UTF-16 encoded command: safe for Chinese paths)."""
    link = Path(link)
    link.parent.mkdir(parents=True, exist_ok=True)
    script = (
        f'$s=(New-Object -ComObject WScript.Shell).CreateShortcut({_ps_quote(link)});'
        f'$s.TargetPath={_ps_quote(target)};$s.WorkingDirectory={_ps_quote(working_dir)};'
        f'$s.IconLocation={_ps_quote(str(icon) + ",0")};$s.Description={_ps_quote(description)};$s.Save()'
    )
    encoded = base64.b64encode(script.encode('utf-16-le')).decode('ascii')
    subprocess.run(
        ['powershell', '-NoProfile', '-NonInteractive', '-ExecutionPolicy', 'Bypass', '-EncodedCommand', encoded],
        check=True,
        timeout=60,
        capture_output=True,
        creationflags=0x08000000 if os.name == 'nt' else 0,  # CREATE_NO_WINDOW
    )
    if not link.is_file():
        raise InstallError('无法创建快捷方式：' + str(link))


class WinRegistry:
    def __init__(self, key_path=UNINSTALL_KEY):
        self.key_path = key_path

    def read(self):
        import winreg

        try:
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, self.key_path) as key:
                values, index = {}, 0
                while True:
                    try:
                        name, value, _ = winreg.EnumValue(key, index)
                    except OSError:
                        return values
                    values[name] = value
                    index += 1
        except FileNotFoundError:
            return None

    def write(self, values):
        import winreg

        with winreg.CreateKey(winreg.HKEY_CURRENT_USER, self.key_path) as key:
            for name, value in values.items():
                kind = winreg.REG_DWORD if isinstance(value, int) else winreg.REG_SZ
                winreg.SetValueEx(key, name, 0, kind, value)

    def delete(self):
        import winreg

        try:
            winreg.DeleteKey(winreg.HKEY_CURRENT_USER, self.key_path)
        except FileNotFoundError:
            pass


@dataclass
class Environment:
    """Where shortcuts/registry go. Tests point these at a temporary folder and a test registry key."""

    start_menu: Path = field(default_factory=lambda: _known_folder(0x0002, '.') / APP_FOLDER)
    desktop: Path = field(default_factory=lambda: _known_folder(0x0010, '.'))
    registry: object = field(default_factory=WinRegistry)
    make_shortcut: object = create_shortcut
    is_running: object = app_is_running

    @classmethod
    def from_environ(cls):
        """GFHELPER_INSTALL_SANDBOX=<folder> redirects shortcuts and the registry key (automated tests only)."""
        sandbox = os.environ.get('GFHELPER_INSTALL_SANDBOX')
        if not sandbox:
            return cls()
        sandbox = Path(sandbox)
        return cls(
            start_menu=sandbox / 'StartMenu' / APP_FOLDER,
            desktop=sandbox / 'Desktop',
            registry=WinRegistry(UNINSTALL_KEY + 'Test'),
        )


# ---------------------------------------------------------------- manifest / payload


def read_manifest(install_dir):
    path = Path(install_dir) / MANIFEST_NAME
    try:
        manifest = json.loads(path.read_text(encoding='utf-8'))
    except (OSError, ValueError):
        return None
    if not isinstance(manifest, dict) or not isinstance(manifest.get('files'), list):
        return None
    return manifest


def _safe_relative(name):
    """Reject absolute paths, '..', drive letters and anything inside data/ (user data is never shipped)."""
    pure = PurePosixPath(name.replace('\\', '/'))
    parts = pure.parts
    if not parts or pure.is_absolute() or any(p in ('..', '') or ':' in p for p in parts):
        return None
    if parts[0].lower() in ('data', MANIFEST_NAME.lower(), STAGING_NAME.lower()):
        return None
    return Path(*parts)


def payload_members(payload):
    with zipfile.ZipFile(payload) as archive:
        if archive.testzip() is not None:
            raise InstallError('安装包已损坏，请重新下载。')
        members = []
        for info in archive.infolist():
            if info.is_dir() or not info.filename.startswith(PAYLOAD_PREFIX):
                continue
            relative = _safe_relative(info.filename[len(PAYLOAD_PREFIX) :])
            if relative is None:
                raise InstallError('安装包内容异常（包含不允许的路径），已停止安装。')
            members.append((info, relative))
    if not any(rel.as_posix() == EXE_NAME for _, rel in members):
        raise InstallError('安装包缺少主程序，已停止安装。')
    return members


def _program_size_kb(folder):
    root = Path(folder)
    return (
        sum(p.stat().st_size for p in root.rglob('*') if p.is_file() and p.relative_to(root).parts[0].lower() != 'data')
        // 1024
    )


def check_target(target):
    target = Path(target)
    if not target.is_absolute():
        raise InstallError('请选择完整的安装路径。')
    if target.parent == target or len(target.parts) < 2:
        raise InstallError('不能直接安装到磁盘根目录，请选择一个文件夹。')
    if target.exists() and not target.is_dir():
        raise InstallError('安装位置是一个文件，请选择文件夹。')
    if target.exists() and any(target.iterdir()) and read_manifest(target) is None:
        entries = {p.name.lower() for p in target.iterdir()}
        if EXE_NAME.lower() in entries:
            raise InstallError(
                '该文件夹里是解压使用的目录版。请安装到其他位置，再用“更多 → 升级与数据迁移”导入旧数据。'
            )
        if not entries <= {'data'}:
            raise InstallError('所选文件夹不是空的，也不是本程序的安装位置。请选择空文件夹或原安装位置。')
    try:
        target.mkdir(parents=True, exist_ok=True)
        probe = target / '.write-test'
        probe.write_bytes(b'')
        probe.unlink()
    except OSError:
        raise InstallError('没有写入该文件夹的权限，请选择其他位置（例如默认位置）。')
    return target


# ---------------------------------------------------------------- install / uninstall


def _remove_listed(install_dir, files):
    """Remove previously installed program files. Never touches data/."""
    failed = []
    for name in files:
        relative = _safe_relative(name)
        if relative is None:
            continue
        path = Path(install_dir) / relative
        try:
            if path.is_file() or path.is_symlink():
                path.unlink()
        except OSError:
            failed.append(name)
    _remove_empty_dirs(install_dir)
    return failed


def _remove_empty_dirs(install_dir):
    root = Path(install_dir)
    for folder in sorted((p for p in root.rglob('*') if p.is_dir()), key=lambda p: len(p.parts), reverse=True):
        relative = folder.relative_to(root)
        if relative.parts[0].lower() == 'data':
            continue
        try:
            folder.rmdir()
        except OSError:
            pass


def install(payload, target, version, env=None, desktop_shortcut=True, progress=lambda fraction, text: None):
    env = env or Environment.from_environ()
    target = check_target(target)
    if env.is_running(target):
        raise InstallError('助手正在运行。请先在助手中选择“彻底退出”（托盘图标也要退出），再继续安装。')
    progress(0.02, '正在检查安装包…')
    members = payload_members(payload)
    staging = target / STAGING_NAME
    shutil.rmtree(staging, ignore_errors=True)
    total = sum(info.file_size for info, _ in members) or 1
    done = 0
    with zipfile.ZipFile(payload) as archive:
        for info, relative in members:
            destination = staging / relative
            destination.parent.mkdir(parents=True, exist_ok=True)
            with archive.open(info) as source, open(destination, 'wb') as output:
                shutil.copyfileobj(source, output, 1024 * 1024)
            done += info.file_size
            progress(0.05 + 0.7 * done / total, '正在解压程序文件…')
    old = read_manifest(target)
    if old:
        progress(0.78, '正在替换旧版本程序文件（保留数据）…')
        failed = _remove_listed(target, old['files'])
        if failed:
            shutil.rmtree(staging, ignore_errors=True)
            raise InstallError('有旧程序文件正在使用，无法替换。请彻底退出助手后重试。')
    progress(0.82, '正在安装程序文件…')
    installed = []
    for _, relative in members:
        destination = target / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        os.replace(staging / relative, destination)
        installed.append(relative.as_posix())
    shutil.rmtree(staging, ignore_errors=True)
    manifest = {
        'product': DISPLAY_NAME,
        'version': version,
        'installed_at': time.strftime('%Y-%m-%d %H:%M:%S'),
        'files': sorted(installed),
    }
    (target / MANIFEST_NAME).write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding='utf-8')
    progress(0.88, '正在创建快捷方式…')
    exe = target / EXE_NAME
    env.make_shortcut(env.start_menu / APP_SHORTCUT, exe, target, exe, DISPLAY_NAME)
    env.make_shortcut(
        env.start_menu / UNINSTALL_SHORTCUT, target / UNINSTALLER_NAME, target, exe, '卸载 ' + DISPLAY_NAME
    )
    desktop_link = env.desktop / APP_SHORTCUT
    if desktop_shortcut:
        env.make_shortcut(desktop_link, exe, target, exe, DISPLAY_NAME)
    progress(0.95, '正在登记到“已安装的应用”…')
    env.registry.write(
        {
            'DisplayName': DISPLAY_NAME,
            'DisplayVersion': version,
            'Publisher': PUBLISHER,
            'Contact': CONTACT,
            'InstallLocation': str(target),
            'DisplayIcon': str(exe),
            'UninstallString': f'"{target / UNINSTALLER_NAME}"',
            'InstallDate': time.strftime('%Y%m%d'),
            'EstimatedSize': _program_size_kb(target),
            'NoModify': 1,
            'NoRepair': 1,
            'DesktopShortcut': 1 if desktop_shortcut else 0,
        }
    )
    progress(1.0, '安装完成。')
    return {'target': str(target), 'files': len(installed), 'upgraded_from': old.get('version') if old else None}


def installed_location(env=None):
    env = env or Environment.from_environ()
    try:
        values = env.registry.read()
    except OSError:
        return None
    location = (values or {}).get('InstallLocation')
    return Path(location) if location else None


def uninstall(target, env=None, delete_data=False, progress=lambda fraction, text: None):
    env = env or Environment.from_environ()
    target = Path(target)
    manifest = read_manifest(target)
    if manifest is None or not (target / EXE_NAME).is_file() and not manifest['files']:
        raise InstallError('没有在该位置找到本程序的安装记录，已停止卸载，未删除任何文件。')
    if env.is_running(target):
        raise InstallError('助手正在运行。请先在助手中选择“彻底退出”（托盘图标也要退出），再卸载。')
    data_remaining = None
    data = target / 'data'
    if delete_data and data.exists():
        progress(0.1, '正在删除数据…')
        from data_purge import purge_data_dir  # bundled from the application source

        data_remaining = purge_data_dir(data, target, timeout=30)
    progress(0.4, '正在删除程序文件…')
    failed = _remove_listed(target, manifest['files'])
    try:
        (target / MANIFEST_NAME).unlink()
    except OSError:
        failed.append(MANIFEST_NAME)
    progress(0.8, '正在删除快捷方式和卸载登记…')
    for link in (env.start_menu / APP_SHORTCUT, env.start_menu / UNINSTALL_SHORTCUT, env.desktop / APP_SHORTCUT):
        try:
            link.unlink()
        except FileNotFoundError:
            pass
        except OSError:
            failed.append(str(link))
    try:
        env.start_menu.rmdir()
    except OSError:
        pass
    env.registry.delete()
    _remove_empty_dirs(target)
    try:
        target.rmdir()
    except OSError:
        pass
    progress(1.0, '卸载完成。')
    return {
        'target': str(target),
        'data_kept': data.exists() and not delete_data,
        'data_remaining': data_remaining or [],
        'failed': failed,
        'folder_removed': not target.exists(),
    }
