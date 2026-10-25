# One-file installer. Environment: GFHELPER_PAYLOAD=<payload.zip>, GFHELPER_ICON=<activity-helper.ico>
import os
from pathlib import Path

root = Path(SPECPATH)
payload = os.environ['GFHELPER_PAYLOAD']
icon = os.environ['GFHELPER_ICON']
a = Analysis([str(root / 'installer_main.py')], pathex=[str(root)],
             datas=[(payload, '.'), (icon, '.')], hiddenimports=[],
             excludes=['playwright', 'PIL', 'uiautomation', 'zxingcpp', 'pystray', 'numpy', 'comtypes'],
             noarchive=False)
pyz = PYZ(a.pure)
exe = EXE(pyz, a.scripts, a.binaries, a.datas, [], name='群活动填表助手-1.1-安装程序',
          debug=False, strip=False, upx=False, console=False, disable_windowed_traceback=False, icon=icon)
