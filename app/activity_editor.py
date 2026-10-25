from ui_layout import fit_window
from design_tokens import FONTS
from diagnostics import describe_error
import json
import tkinter as tk
from tkinter import ttk, messagebox
from appearance import themed


def edit_activity(app):
    selection = app.table.selection()
    if len(selection) != 1:
        messagebox.showinfo('选择任务', '请先选中一个活动任务。')
        return
    url = selection[0]
    row = app.store.task(url)
    win = tk.Toplevel(app.root)
    win.transient(app.root)
    win.title('活动备注、准备清单与提醒')
    fit_window(win, 790, 670)
    outer = ttk.Frame(win, padding=16)
    outer.pack(fill='both', expand=True)
    ttk.Label(outer, text=row['title'] or '活动收集表', font=FONTS['section'], wraplength=730).pack(
        anchor='w', pady=(0, 12)
    )
    tabs = ttk.Notebook(outer)
    tabs.pack(fill='both', expand=True)
    notes_tab = ttk.Frame(tabs, padding=16)
    tabs.add(notes_tab, text='备注与意向')
    intent = tk.StringVar(value=row['intent'])
    ttk.Label(notes_tab, text='参加意向（只作记录，不改变报名或任务状态）').pack(anchor='w')
    ttk.Combobox(
        notes_tab, textvariable=intent, values=['未决定', '想参加', '待考虑', '不参加', '已参加'], state='readonly'
    ).pack(anchor='w', pady=8)
    ttk.Label(notes_tab, text='活动备注').pack(anchor='w')
    notes = tk.Text(notes_tab, wrap='word', height=10, font=FONTS['body'])
    notes.pack(fill='both', expand=True, pady=8)
    notes.insert('1.0', row['activity_notes'])
    checklist_tab = ttk.Frame(tabs, padding=16)
    tabs.add(checklist_tab, text='准备清单')
    ttk.Label(checklist_tab, text='双击切换完成状态。勾选只用于记录，不代表报名成功。').pack(anchor='w')
    tree = ttk.Treeview(checklist_tab, columns=('done', 'text'), show='headings', height=8, selectmode='browse')
    tree.heading('done', text='完成', anchor='w')
    tree.heading('text', text='准备事项', anchor='w')
    tree.column('done', width=65, stretch=False)
    tree.column('text', width=550)
    tree.pack(fill='both', expand=True, pady=8)
    for item in json.loads(row['checklist']):
        tree.insert('', 'end', values=('✓' if item['done'] else '○', item['text']))

    def toggle(event=None):
        if tree.selection():
            iid = tree.selection()[0]
            v = tree.item(iid, 'values')
            tree.item(iid, values=('○' if v[0] == '✓' else '✓', v[1]))

    tree.bind('<Double-1>', toggle)
    item_text = tk.StringVar()
    ttk.Entry(checklist_tab, textvariable=item_text).pack(fill='x')

    def add():
        value = item_text.get().strip()
        if value:
            tree.insert('', 'end', values=('○', value))
            item_text.set('')

    bar = ttk.Frame(checklist_tab)
    bar.pack(fill='x', pady=8)
    ttk.Button(bar, text='添加事项', command=add).pack(side='left')
    ttk.Button(bar, text='切换完成', command=toggle).pack(side='left', padx=8)
    ttk.Button(bar, text='删除选中', command=lambda: tree.delete(*tree.selection())).pack(side='left')
    reminder_tab = ttk.Frame(tabs, padding=16)
    tabs.add(reminder_tab, text='专属提醒')
    names = {'inherit': '沿用全局', 'custom': '自定义', 'off': '关闭本任务提醒'}
    mode = tk.StringVar(value=names[row['reminder_mode']])
    minutes = tk.StringVar(value=','.join(map(str, json.loads(row['task_minutes']))))
    ttk.Label(reminder_tab, text='本任务的报名截止提醒').pack(anchor='w')
    ttk.Combobox(reminder_tab, textvariable=mode, values=list(names.values()), state='readonly', width=25).pack(
        anchor='w', pady=8
    )
    ttk.Label(reminder_tab, text='自定义提前量（分钟），例如 60,30,10').pack(anchor='w', pady=(10, 5))
    ttk.Entry(reminder_tab, textvariable=minutes).pack(fill='x')
    ttk.Label(
        reminder_tab,
        text='自定义会替代全局截止提醒设置；需先保存有效截止时间。\n已处理、忽略、暂缓任务仍不触发提醒。\n同一截止时间、同一提前量不会重复提醒。\n“10分钟后再提醒”可单独提醒此任务，不需要截止时间。',
        wraplength=670,
    ).pack(anchor='w', pady=18)

    def save(close=True):
        try:
            if item_text.get().strip():
                add()
            items = [
                {'text': tree.item(iid, 'values')[1], 'done': tree.item(iid, 'values')[0] == '✓'}
                for iid in tree.get_children()
            ]
            app.store.save_activity(
                url,
                notes.get('1.0', 'end-1c'),
                intent.get(),
                items,
                {v: k for k, v in names.items()}[mode.get()],
                minutes.get(),
            )
            app.next_alert_check = 0
            app.refresh_table()
            app.emit('活动备注、准备清单和专属提醒已保存。', state='success')
            if close:
                win.destroy()
            return True
        except Exception as e:
            messagebox.showerror('无法保存', describe_error(e), parent=win)
            return False

    def later():
        if mode.get() == names['off']:
            messagebox.showinfo('本任务提醒已关闭', '请改为沿用全局或自定义后，再安排提醒。', parent=win)
            return
        if save(False):
            task = app.store.task(url)
            if task['state'] == 'done' or task['disposition'] != 'active':
                messagebox.showinfo('任务不可提醒', '请先恢复该任务为进行中状态。', parent=win)
                return
            app.store.remind_later(url, 10)
            app.emit('已安排10分钟后再次提醒此任务。', state='success')
            win.destroy()

    ttk.Button(reminder_tab, text='保存并在10分钟后再提醒', command=later).pack(anchor='w')
    footer = ttk.Frame(outer)
    footer.pack(fill='x', pady=(14, 0))
    ttk.Button(footer, text='保存', style='Primary.TButton', command=save).pack(side='right')
    ttk.Button(footer, text='取消', command=win.destroy).pack(side='right', padx=8)
    themed(app, win)
