"""One-use approval for immediately saved sheet content, with visible verification."""

import queue, threading, time
import tkinter as tk
from tkinter import ttk
from ui_layout import fit_window, bind_wrap, ResizeUpdate
from design_tokens import dp
from ui_status import StatusVar, StatusLabel
from appearance import themed
from diagnostics import describe_error
from sheet_fill import make_sheet_plan, validate_approval


def show_sheet_fill(app, url, data, profile, header_row, valid=lambda: True, on_dispatch=lambda: None):
    try:
        plan = make_sheet_plan(data, profile, header_row)
    except Exception as error:
        app.status.set(describe_error(error), state='warning')
        return
    win = tk.Toplevel(app.root)
    win.transient(app.root)
    win.title('腾讯表格 · 确认写入')
    fit_window(win, 900, 660)
    frame = ttk.Frame(win, padding=16)
    frame.pack(fill='both', expand=True)
    frame.columnconfigure(0, weight=1)
    frame.rowconfigure(2, weight=1)
    summary = ttk.Label(
        frame,
        text=f"{plan['document_title']} · {plan['worksheet']} · 第{plan['target_row']}行\n重新读取区域：{plan['range']}。写入可能即时保存，不能保证撤回。",
        wraplength=820,
    )
    summary.grid(row=0, column=0, sticky='ew')
    feedback = StatusVar(win, '请核对本次资料；空白跳过，未知列不自动填写。', state='warning')
    feedback_label = StatusLabel(frame, feedback, wraplength=820)
    feedback_label.grid(row=1, column=0, sticky='ew', pady=8)
    listing = ttk.Frame(frame)
    listing.grid(row=2, column=0, sticky='nsew')
    listing.rowconfigure(0, weight=1)
    listing.columnconfigure(0, weight=1)
    table = ttk.Treeview(listing, columns=('address', 'title', 'value', 'state'), show='headings', height=9)
    for key, label, width in [
        ('address', '目标单元格', 100),
        ('title', '表头', 180),
        ('value', '本次填写', 300),
        ('state', '状态', 230),
    ]:
        table.heading(key, text=label, anchor='w')
        table.column(key, width=width, minwidth=60)
    table.grid(row=0, column=0, sticky='nsew')
    vs = ttk.Scrollbar(listing, orient='vertical', command=table.yview)
    vs.grid(row=0, column=1, sticky='ns')
    table.configure(yscrollcommand=vs.set)
    hs = ttk.Scrollbar(listing, orient='horizontal', command=table.xview)
    hs.grid(row=1, column=0, sticky='ew')
    table.configure(xscrollcommand=hs.set)
    cells = {c['address']: c for c in plan['cells']}
    values = {c['address']: c['value'] for c in plan['cells'] if c['allowed']}
    for cell in plan['cells']:
        table.insert(
            '',
            'end',
            iid=cell['address'],
            values=(
                cell['address'],
                cell['title'],
                cell['value'],
                '待确认／空白跳过' if cell['allowed'] else '未匹配，不填写',
            ),
        )
    edit_label = ttk.Label(frame, text='选中已匹配的列，可修改本次内容；不会改变全局资料。', wraplength=820)
    edit_label.grid(row=3, column=0, sticky='ew', pady=(10, 4))
    edit = ttk.Entry(frame, state='disabled')
    edit.grid(row=4, column=0, sticky='ew')
    selected = [None]
    req = [None]
    timer = [None]
    used = [False]

    def flush():
        if selected[0] in values:
            values[selected[0]] = edit.get()
            cell = cells[selected[0]]
            table.item(selected[0], values=(selected[0], cell['title'], values[selected[0]], '待确认／空白跳过'))

    def select(*_):
        if used[0]:
            return
        flush()
        ids = table.selection()
        selected[0] = ids[0] if ids else None
        edit.configure(state='normal')
        edit.delete(0, 'end')
        if selected[0] in values:
            edit.insert(0, values[selected[0]])
        else:
            edit.configure(state='disabled')

    table.bind('<<TreeviewSelect>>', select)
    checked = tk.BooleanVar(value=False)
    check_style = f'SheetLayout{win.winfo_id()}.TCheckbutton'
    style = ttk.Style(win)
    style.configure(check_style, wraplength=820)
    layout_check = ttk.Checkbutton(
        frame,
        text='我已核对：范围包含全部报名列；目标区域没有隐藏／筛选、合并或保护。',
        variable=checked,
        style=check_style,
    )
    layout_check.grid(row=5, column=0, sticky='ew', pady=10)
    wrap_check = ResizeUpdate(
        frame, lambda event: style.configure(check_style, wraplength=max(1, event.width - 32 - dp(win, 32)))
    )
    frame.bind('<Configure>', wrap_check.request, add='+')
    for label in (summary, feedback_label, edit_label):
        bind_wrap(frame, label, lambda event: max(1, event.width - 32))
    bottom = ttk.Frame(frame)
    bottom.grid(row=6, column=0, sticky='ew')

    def close():
        if req[0]:
            req[0]['cancel'].set()
            feedback.set('正在停止。已写入内容可能保存，请等待结果并在网页核对。', state='warning')
            return
        win.destroy()

    def poll():
        timer[0] = None
        if not win.winfo_exists() or not req[0]:
            return
        request = req[0]
        try:
            reply = request['reply'].get_nowait()
        except queue.Empty:
            if time.monotonic() - request['created'] >= 120:
                request['cancel'].set()
                req[0] = None
                feedback.set(
                    '核验等待已达120秒，已请求停止；部分内容可能保存，请在网页核对，不要重复执行。', state='error'
                )
                return
            timer[0] = win.after(100, poll)
            return
        req[0] = None
        if reply.get('error'):
            feedback.set(reply['error'], state='error')
        else:
            feedback.set(f"已写入并回读核验 {len(reply['written'])} 格。是否完成报名请在网页核对。", state='success')

    def confirm():
        if used[0] or req[0]:
            return
        try:
            flush()
            if not checked.get():
                raise ValueError('请先核对范围及隐藏、筛选、合并和保护状态，勾选确认。')
            if not valid():
                raise ValueError('读取范围或表头选择已变化，请重新读取并预览。')
            if not app.processing.is_set():
                raise ValueError('请先继续任务处理。')
            if not app.browser_thread or not app.browser_thread.is_alive():
                raise ValueError('请先在助手浏览器打开并登录此表格。')
            approved = validate_approval(plan, values)
            request = {
                'url': url,
                'action': 'write',
                'plan': plan,
                'approved': approved,
                'confirmed_layout': True,
                'cancel': threading.Event(),
                'reply': queue.Queue(maxsize=1),
                'created': time.monotonic(),
            }
            app.sheet_requests.put_nowait(request)
            req[0] = request
            used[0] = True
            confirm_button.configure(state='disabled')
            edit.configure(state='disabled')
            on_dispatch()
            feedback.set('正在重新读取并核对，随后写入和回读。请勿同时操作网页。', state='running')
            timer[0] = win.after(100, poll)
        except queue.Full:
            feedback.set('已有表格请求等待执行，请稍候再试。', state='warning')
        except Exception as error:
            feedback.set(describe_error(error), state='error')

    ttk.Button(bottom, text='取消／关闭', command=close).pack(side='right')
    confirm_button = ttk.Button(
        bottom,
        text='确认写入（可能立即保存）',
        style='Primary.TButton',
        command=confirm,
        state='normal' if plan['page_bound'] else 'disabled',
    )
    confirm_button.pack(side='right', padx=8)
    if not plan['page_bound']:
        feedback.set('粘贴仅用于资料预览，尚未绑定真实工作表。自动写入须先使用“读取网页表头”。', state='warning')
    win.protocol('WM_DELETE_WINDOW', close)

    def destroyed(event):
        if event.widget is not win:
            return
        if req[0]:
            req[0]['cancel'].set()
        if timer[0]:
            try:
                win.after_cancel(timer[0])
            except tk.TclError:
                pass
        values.clear()
        cells.clear()

    win.bind('<Destroy>', destroyed, add='+')
    themed(app, win)
