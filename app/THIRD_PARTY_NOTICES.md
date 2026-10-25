# 第三方组件清单

以下版本来自构建环境的安装元数据，许可证原文保留在 third_party/licenses；这是分发清单，不是完整法律意见或漏洞扫描结果。zxing-cpp 自 v0.16 引入，用于本地判断图片是否包含二维码；不上传图片，也不记录二维码内容。v0.19 无新增第三方依赖。

| 组件 | 版本 | 安装包声明的许可 | 用途 | 许可目录 |
|---|---|---|---|---|
| altgraph | 0.17.5 | MIT | 构建工具 | third_party/licenses/altgraph |
| comtypes | 1.4.17 | MIT | 运行依赖 | third_party/licenses/comtypes |
| greenlet | 3.5.6 | MIT AND PSF-2.0 | 运行依赖 | third_party/licenses/greenlet |
| packaging | 26.3 | Apache-2.0 OR BSD-2-Clause | 构建工具 | third_party/licenses/packaging |
| pefile | 2024.8.26 | MIT | 构建工具 | third_party/licenses/pefile |
| pillow | 12.3.0 | MIT-CMU | 运行依赖 | third_party/licenses/pillow |
| playwright | 1.63.0 | Apache-2.0 | 运行依赖 | third_party/licenses/playwright |
| pyee | 13.0.1 | MIT | 运行依赖 | third_party/licenses/pyee |
| pyinstaller | 6.22.3 | 见所附许可证全文 | 构建工具 | third_party/licenses/pyinstaller |
| pyinstaller-hooks-contrib | 2026.7 | 见所附许可证 | 构建工具 | third_party/licenses/pyinstaller-hooks-contrib |
| pystray | 0.19.5 | LGPLv3 | 运行依赖 | third_party/licenses/pystray |
| pywin32-ctypes | 0.2.3 | BSD-3-Clause | 构建工具 | third_party/licenses/pywin32-ctypes |
| setuptools | 84.0.0 | MIT | 构建工具 | third_party/licenses/setuptools |
| six | 1.17.0 | MIT | 运行依赖 | third_party/licenses/six |
| typing_extensions | 4.16.0 | PSF-2.0 | 运行依赖 | third_party/licenses/typing_extensions |
| zxing-cpp | 3.1.1 | Apache-2.0 | 本地二维码识别 | third_party/licenses/zxing-cpp |
| uiautomation | 2.0.29 | Apache 2.0 | 运行依赖 | third_party/licenses/uiautomation |

Python 3.12.14、Tcl/Tk 随可执行文件分发，许可见 Python-Tcl-Tk 目录；Playwright 附带 Node 驱动及其许可通知，原文一并保留。Microsoft Edge 由用户电脑提供，不随本包分发。聊天客户端同样不随本包分发。

pystray 为 LGPL v3 组件，源码与许可证随包提供，替换及重新构建方法见 BUILD.md。PyInstaller 构建工具的 bootloader 例外以其原始许可证为准。本软件与微信、腾讯文档、问卷星的权利人没有官方隶属或授权关系；当前可读取 群聊文字链接，并打开腾讯收集表、腾讯在线表格和金山文档；自动填写仍限腾讯收集表。
