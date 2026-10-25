"""Offline, readable notices. No external navigation or arbitrary file opening."""

from version import APP_VERSION, ISSUES_URL
from design_tokens import FONTS
from pathlib import Path
import sys
import tkinter as tk
from tkinter import ttk
from appearance import themed
from ui_layout import fit_window

DOCUMENTS = {
    '功能范围': '功能支持说明.txt',
    '软件声明': '软件声明.txt',
    '隐私说明': '隐私说明.txt',
    '第三方清单': 'THIRD_PARTY_NOTICES.md',
}


def resource_root():
    return Path(getattr(sys, '_MEIPASS', Path(__file__).resolve().parent))


def read_document(name):
    if name not in DOCUMENTS:
        raise ValueError('不支持的声明文档')
    return read_text(resource_root() / DOCUMENTS[name])


def read_text(path):
    try:
        if path.stat().st_size > 4 * 1024 * 1024:
            return '文档过大，请查看发行包中的原文。'
        return path.read_text(encoding='utf-8', errors='replace')
    except OSError:
        return '无法读取此文档，请检查是否完整解压软件包，或到 ' + ISSUES_URL + ' 反馈。'


def text_panel(parent):
    frame = ttk.Frame(parent)
    text = tk.Text(frame, wrap='word', font=FONTS['body'], padx=14, pady=12, borderwidth=0)
    scroll = ttk.Scrollbar(frame, orient='vertical', command=text.yview)
    text.configure(yscrollcommand=scroll.set)
    scroll.pack(side='right', fill='y')
    text.pack(fill='both', expand=True)
    return frame, text


def set_content(widget, content):
    widget.configure(state='normal')
    widget.delete('1.0', 'end')
    widget.insert('1.0', content)
    widget.configure(state='disabled')


def show_about(app):
    previous = getattr(app, 'about_window', None)
    if previous and previous.winfo_exists():
        previous.lift()
        return
    win = tk.Toplevel(app.root)
    app.about_window = win
    win.title(f'关于与软件声明 · v{APP_VERSION}')
    win.transient(app.root)
    fit_window(win, 860, 660, (600, 420))
    footer = ttk.Frame(win, padding=(14, 8))
    footer.pack(side='bottom', fill='x')
    ttk.Label(footer, text='Designed by Chen Zhuo  ·  问题反馈：' + ISSUES_URL).pack(side='left')
    ttk.Button(footer, text='关闭', command=win.destroy).pack(side='right')
    ttk.Label(win, text='独立的第三方辅助工具 · 非平台官方产品', font=FONTS['section']).pack(
        anchor='w', padx=16, pady=12
    )
    notebook = ttk.Notebook(win)
    notebook.pack(fill='both', expand=True, padx=14, pady=(0, 12))
    for label in DOCUMENTS:
        frame, text = text_panel(notebook)
        notebook.add(frame, text=label)
        set_content(text, read_document(label))
    license_frame = ttk.Frame(notebook)
    notebook.add(license_frame, text='许可证原文')
    paths = sorted((resource_root() / 'third_party' / 'licenses').rglob('*'))
    choices = {str(p.relative_to(resource_root() / 'third_party' / 'licenses')): p for p in paths if p.is_file()}
    choice = tk.StringVar()
    chooser = ttk.Combobox(license_frame, textvariable=choice, values=list(choices), state='readonly')
    chooser.pack(fill='x', padx=8, pady=8)
    frame, text = text_panel(license_frame)
    frame.pack(fill='both', expand=True)

    def selected(_=None):
        path = choices.get(choice.get())
        set_content(text, read_text(path) if path else '暂无可显示的许可文件，请使用完整发行包。')

    chooser.bind('<<ComboboxSelected>>', selected)
    if choices:
        choice.set(next(iter(choices)))
    selected()
    themed(app, win)
