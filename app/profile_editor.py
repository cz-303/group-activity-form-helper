import tkinter as tk
from tkinter import ttk, messagebox
from core import validate_custom_fields


def build_custom_editor(notebook, initial, parent=None):
    frame = parent if parent is not None else ttk.Frame(notebook, padding=16)
    frame.configure(padding=16)
    if parent is None:
        notebook.add(frame, text='自定义资料')
    ttk.Label(
        frame,
        text='题目名称需与表单一致（忽略空格、冒号和必填星号）。\n仅填写空白文本题，不覆盖已填答案；密码、验证码和附件不自动填写。',
        wraplength=580,
    ).pack(anchor='w')
    listing = ttk.Frame(frame)
    listing.pack(fill='both', expand=True, pady=8)
    table = ttk.Treeview(listing, columns=('name', 'value'), show='headings', height=5, selectmode='browse')
    table.heading('name', text='题目名称', anchor='w')
    table.heading('value', text='填写内容', anchor='w')
    table.column('name', width=150)
    table.column('value', width=350)
    table.pack(side='left', fill='both', expand=True)
    scroll = ttk.Scrollbar(listing, orient='vertical', command=table.yview)
    scroll.pack(side='right', fill='y')
    table.configure(yscrollcommand=scroll.set)
    data = dict(initial)
    selected = [None]
    name = tk.StringVar()
    ttk.Label(frame, text='题目名称，例如“专业”或“研究方向”').pack(anchor='w')
    ttk.Entry(frame, textvariable=name).pack(fill='x', pady=(2, 5))
    value = tk.Text(frame, height=3, wrap='word')
    value.pack(fill='x')

    def redraw():
        table.delete(*table.get_children())
        for key, text in data.items():
            table.insert('', 'end', iid=key, values=(key, text.replace('\n', ' ')[:100]))

    def select(event):
        picked = table.selection()
        if not picked:
            return
        selected[0] = picked[0]
        name.set(picked[0])
        value.delete('1.0', 'end')
        value.insert('1.0', data[picked[0]])

    table.bind('<<TreeviewSelect>>', select)

    def clear():
        selected[0] = None
        name.set('')
        value.delete('1.0', 'end')
        table.selection_remove(table.selection())

    def apply():
        title = name.get().strip()
        answer = value.get('1.0', 'end-1c')
        if not title and not answer.strip() and selected[0] is None:
            return
        candidate = dict(data)
        if selected[0]:
            candidate.pop(selected[0], None)
        if title in candidate:
            raise ValueError('同名资料已存在，请选中该项后编辑。')
        candidate[title] = answer
        checked = validate_custom_fields(candidate)
        data.clear()
        data.update(checked)
        redraw()
        clear()

    def add():
        try:
            apply()
        except ValueError as e:
            messagebox.showerror('无法保存资料', str(e), parent=frame.winfo_toplevel())

    def delete():
        if selected[0]:
            data.pop(selected[0], None)
            redraw()
            clear()

    bar = ttk.Frame(frame)
    bar.pack(fill='x', pady=(8, 0))
    for label, command in [('添加 / 更新', add), ('新增一项', clear), ('删除选中项', delete)]:
        ttk.Button(bar, text=label, command=command).pack(side='left', padx=(0, 8))
    redraw()

    def read():
        apply()
        return dict(data)

    return read
