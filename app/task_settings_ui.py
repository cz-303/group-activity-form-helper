"""Presentation of existing task settings; validation and saving stay with the caller."""

import tkinter as tk
from tkinter import ttk

from design_tokens import FONTS, dp
from ui_layout import bind_wrap


def build_task_settings(frame, config, variables, fields):
    def note(parent, text, row):
        label = ttk.Label(parent, text=text, style='Muted.TLabel', wraplength=dp(frame, 560), justify='left')
        label.grid(row=row, column=0, columnspan=2, sticky='ew', pady=dp(frame, 8))
        bind_wrap(parent, label, lambda event: max(dp(frame, 160), event.width - dp(frame, 32)))
        return label

    ttk.Label(frame, text='捕获后如何处理', font=FONTS['strong']).grid(
        row=0, column=0, columnspan=2, sticky='w', pady=(0, dp(frame, 8))
    )
    for row, key, label in (
        (1, 'auto_open_forms', '收集表：捕获链接后自动跳转'),
        (2, 'auto_open_documents', '在线表格／文档：捕获链接后自动跳转'),
        (4, 'auto_resume', '软件启动后自动恢复任务处理（仍遵守上述跳转设置）'),
    ):
        value = tk.BooleanVar(frame, value=config[key])
        variables[key] = value
        ttk.Checkbutton(frame, text=label, variable=value).grid(
            row=row, column=0, columnspan=2, sticky='w', pady=dp(frame, 8)
        )
    note(
        frame,
        '关闭自动跳转后，仍保存链接、通知和提醒，可在清单手动排队打开。\n'
        '跳转需先“继续处理”；仅打开网页，不代表填写或提交。\n'
        '重新开启会继续处理历史待办，填写仍须确认。',
        3,
    )

    advanced = ttk.Frame(frame, name='task_advanced')
    advanced.columnconfigure(1, weight=1)
    for row, (key, label) in enumerate(fields):
        ttk.Label(advanced, text=label).grid(row=row, column=0, sticky='w', padx=(0, dp(frame, 12)), pady=dp(frame, 8))
        value = tk.StringVar(advanced, value=str(config[key]))
        variables[key] = value
        ttk.Entry(advanced, textvariable=value).grid(row=row, column=1, sticky='ew', pady=dp(frame, 8))
    advanced.grid(row=6, column=0, columnspan=2, sticky='ew')
    advanced.grid_remove()

    rules = ttk.Frame(frame, name='task_rules')
    rules.columnconfigure(0, weight=1)
    note(
        rules,
        '在线表格／文档包括腾讯表格和金山分享文档。\n'
        '关闭某类自动跳转后，该类历史待办、失败重试及暂缓到期任务均等待手动打开。\n'
        '已开始打开的网页可能完成加载，已打开页面不会关闭。\n'
        '手动排队许可不跨重启保留；自动跳转关闭时，手动打开失败须再次手动排队。\n'
        '“已打开”和“已处理”不会因重启自动重复打开。',
        0,
    )
    rules.grid(row=8, column=0, columnspan=2, sticky='ew')
    rules.grid_remove()

    def toggle(panel, button, title):
        if panel.winfo_manager():
            focus = frame.focus_get()
            if focus and str(focus).startswith(str(panel) + '.'):
                button.focus_set()
            panel.grid_remove()
            button.configure(text='展开' + title)
        else:
            panel.grid()
            button.configure(text='收起' + title)

    advanced_button = ttk.Button(frame, text='展开高级选项')
    advanced_button.configure(command=lambda: toggle(advanced, advanced_button, '高级选项'))
    advanced_button.grid(row=5, column=0, columnspan=2, sticky='w', pady=(dp(frame, 16), dp(frame, 8)))
    rules_button = ttk.Button(frame, text='展开打开与重试规则')
    rules_button.configure(command=lambda: toggle(rules, rules_button, '打开与重试规则'))
    rules_button.grid(row=7, column=0, columnspan=2, sticky='w', pady=(dp(frame, 16), dp(frame, 8)))
