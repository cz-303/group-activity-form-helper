# Rebuild from this directory with Python 3.12 and requirements-build.txt.
from pathlib import Path
from PyInstaller.utils.hooks import collect_all

root = Path(SPECPATH)
data, binaries, hidden = collect_all('uiautomation')
data.append((str(root/'activity-helper.ico'), '.'))
for name in ('软件声明.txt','隐私说明.txt','THIRD_PARTY_NOTICES.md','功能支持说明.txt'):
    data.append((str(root/name), '.'))
data.append((str(root/'third_party'/'licenses'), 'third_party/licenses'))
a = Analysis([str(root/'app.py')],
             pathex=[str(root/'third_party'/'pystray-source'),str(root)],
             binaries=binaries,datas=data,hiddenimports=hidden+['pystray._win32'],
             hookspath=[],hooksconfig={},runtime_hooks=[],excludes=[],noarchive=False)
pyz = PYZ(a.pure)
exe = EXE(pyz,a.scripts,[],exclude_binaries=True,name='群活动填表助手',
          debug=False,strip=False,upx=False,console=False,
          disable_windowed_traceback=True,icon=str(root/'activity-helper.ico'))

collection = COLLECT(exe,a.binaries,a.datas,strip=False,upx=False,name='群活动填表助手')
