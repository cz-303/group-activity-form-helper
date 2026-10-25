# One-file uninstaller copied into the install folder as 卸载.exe.
# Environment: GFHELPER_APP_SOURCE=<app source folder with data_purge.py>, GFHELPER_ICON=<activity-helper.ico>
import os
from pathlib import Path

root = Path(SPECPATH)
source = os.environ['GFHELPER_APP_SOURCE']
icon = os.environ['GFHELPER_ICON']
a = Analysis([str(root / 'uninstaller_main.py')], pathex=[str(root), source],
             datas=[(icon, '.')], hiddenimports=['data_purge'],
             excludes=['playwright', 'PIL', 'uiautomation', 'zxingcpp', 'pystray', 'numpy', 'comtypes'],
             noarchive=False)
pyz = PYZ(a.pure)
exe = EXE(pyz, a.scripts, a.binaries, a.datas, [], name='卸载',
          debug=False, strip=False, upx=False, console=False, disable_windowed_traceback=False, icon=icon)
