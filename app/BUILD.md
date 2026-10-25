# 从源码构建

安装程序源码在仓库的 installer/ 目录。

需要 Windows x64、Python 3.12 和 Microsoft Edge。进入本目录后：

```powershell
py -3.12 -m venv .venv
.venv\Scripts\python.exe -m pip install -r requirements-build.txt
.venv\Scripts\python.exe -B -m unittest discover -s tests
.venv\Scripts\python.exe -m PyInstaller --noconfirm --clean build.spec
dist\群活动填表助手\群活动填表助手.exe --self-test bundle-test.json
```

构建产物在 dist\群活动填表助手，必须保留同目录的 _internal；源码运行入口是 app.py。测试只使用临时数据、本地模拟表单、模拟消息树，不提交真实报名。部分测试需要已安装的 Edge，并会短暂创建测试窗口和托盘图标。

third_party/pystray-source 包含本包使用的 pystray 完整 Python 模块源文件及原始许可证。可修改或替换该目录中的库，build.spec 优先使用该目录重新组合并构建应用。允许为了修改第三方 LGPL 库以及调试这些修改而进行必要的重新组合和逆向分析；不限制第三方许可证赋予的权利。其他组件及许可证见 THIRD_PARTY_NOTICES.md 和 third_party/licenses。

禁止把自己的 data 目录、浏览器配置、迁移包或真实测试资料放入发布包。源码与依赖许可一并随软件分发。本文件提供构建方法，不代表已完成所有商业分发或软著权属审查。
