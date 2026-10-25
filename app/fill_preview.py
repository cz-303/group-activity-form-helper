"""Read-only plans, explicit one-use approval, and fresh page checks before filling."""

from ui_layout import fit_window, bind_wrap
from design_tokens import FONTS
import json, hashlib, copy, re
import tkinter as tk
from tkinter import ttk, messagebox
from core import field_key, normalize_title, validate_custom_fields, canonical_url
from appearance import themed
from ui_status import StatusVar, StatusLabel

INPUTS = 'textarea, input:not([type]), input[type=text], input[type=email], input[type=tel], input[type=number], input[type=url]'


def update_preview_button(app):
    from ui import update_task_selection

    update_task_selection(app)
    from providers.tencent_sheet import is_tencent_sheet

    selected = app.table.selection()
    app.preview_button.configure(
        state='normal' if len(selected) == 1 else 'disabled',
        text='表头读取预览' if len(selected) == 1 and is_tencent_sheet(selected[0]) else '填写前预览',
    )


def build_plan(page, profile):
    cards = page.locator('.question-main-content')
    titles = []
    for i in range(cards.count()):
        t = cards.nth(i).locator('.question-title')
        titles.append(t.inner_text().strip() if t.count() == 1 else '')
    custom = {normalize_title(k): v for k, v in validate_custom_fields(profile.get('custom_fields', {})).items()}
    keys = [field_key(t) or ('custom:' + normalize_title(t) if normalize_title(t) in custom else None) for t in titles]
    rows = []
    for i, title in enumerate(titles):
        key = keys[i]
        inputs = cards.nth(i).locator(INPUTS)
        status = '需在网页手动填写'
        allowed = False
        value = ''
        if title and not re.search('密码|验证码|口令|密钥|令牌', title) and inputs.count() == 1:
            c = inputs.first
            if not c.is_visible() or not c.is_enabled() or not c.is_editable():
                status = '暂不可编辑，请先登录或核对开放状态'
            elif c.input_value().strip():
                status = '已有内容，不覆盖'
            elif not key or keys.count(key) != 1:
                status = '未匹配资料或题目重复，请在网页填写'
            else:
                allowed = True
                value = custom[normalize_title(title)] if key.startswith('custom:') else profile.get(key, '')
                status = '等待确认' if value else '资料为空，可在预览中补充'
        rows.append(
            {
                'index': i,
                'title': title or '未识别题目',
                'key': key,
                'value': value,
                'allowed': allowed,
                'status': status,
            }
        )
    return rows


def signature(plan):
    return hashlib.sha256(json.dumps(plan, ensure_ascii=False, sort_keys=True).encode()).hexdigest()


def apply_approved(page, url, profile, plan, approved):
    if canonical_url(page.url) != url or signature(build_plan(page, profile)) != signature(plan):
        raise ValueError('网页题目、已有内容或资料已变化，请重新预览确认。')
    # Validate the whole approval before any write.
    if not isinstance(approved, dict) or any(
        i not in {r['index'] for r in plan if r['allowed']} or not isinstance(v, str) or len(v) > 4000
        for i, v in approved.items()
    ):
        raise ValueError('预览确认数据无效。')
    filled = 0
    for row in plan:
        value = approved.get(row['index'], '').strip()
        if not row['allowed'] or not value:
            continue
        if canonical_url(page.url) != url:
            raise ValueError('页面已跳转，停止填写，请重新核对。')
        card = page.locator('.question-main-content').nth(row['index'])
        title = card.locator('.question-title')
        inputs = card.locator(INPUTS)
        if title.count() != 1 or title.inner_text().strip() != row['title'] or inputs.count() != 1:
            raise ValueError('页面结构改变，已停止后续填写。')
        control = inputs.first
        if (
            not control.is_visible()
            or not control.is_enabled()
            or not control.is_editable()
            or control.input_value().strip()
        ):
            continue
        control.fill(value, timeout=1500)
        filled += int(control.input_value() == value)
    return filled


def show_fill_preview(app):
    selected = app.table.selection()
    if len(selected) != 1:
        messagebox.showinfo('请选择任务', '请先选择一个已打开的任务。')
        return
    url = selected[0]
    from providers.tencent_sheet import is_tencent_sheet

    if is_tencent_sheet(url):
        from sheet_header_ui import show_headers

        show_headers(app)
        return
    if not canonical_url(url):
        messagebox.showinfo(
            '在线文档填写尚未启用',
            '此版本可捕获、保存和打开该文档。表格读取及填写适配仍在验证，请暂时在网页填写。在线表格输入后可能立即保存。',
        )
        return
    with app.preview_lock:
        entry = copy.deepcopy(app.previews.get(url))
    if not entry or not entry.get('plan'):
        messagebox.showinfo('尚无预览', '请先打开表单并登录，等待题目加载；暂停处理时不会生成新预览。')
        return
    if entry['state'] == 'applied':
        with app.preview_lock:
            app.previews.pop(url, None)
        messagebox.showinfo('重新检查', '已请求重新读取剩余空白字段，请稍候再打开预览。')
        return
    win = tk.Toplevel(app.root)
    win.transient(app.root)
    win.title('填写前预览 · 不会提交')
    fit_window(win, 880, 640)
    frame = ttk.Frame(win, padding=(24, 20, 24, 16))
    frame.pack(fill='both', expand=True)
    frame.columnconfigure(0, weight=1)
    frame.rowconfigure(2, weight=1)
    header = ttk.Frame(frame)
    header.grid(row=0, column=0, sticky='ew')
    ttk.Label(header, text='填写前预览', style='Dialog.TLabel').pack(anchor='w')
    hint = ttk.Label(
        header,
        text='仅确认本次填写。可修改待填内容或清空以跳过；已有答案、附件和选择题保留在网页中处理。',
        wraplength=830,
        style='Muted.TLabel',
    )
    hint.pack(anchor='w', fill='x', pady=(4, 0))
    bind_wrap(frame, hint, lambda event: max(100, event.width - 48))
    feedback = StatusVar(win, '请核对资料；本次操作只填写，最终由你在网页提交。', state='warning')
    status = StatusLabel(frame, feedback, wraplength=800)
    status.grid(row=1, column=0, sticky='ew', pady=(8, 0))
    bind_wrap(frame, status, lambda event: max(100, event.width - 32))
    table = ttk.Treeview(frame, columns=('title', 'value', 'state'), show='headings', height=10, selectmode='browse')
    for name, label, width in [('title', '表单题目', 180), ('value', '准备填写', 320), ('state', '状态', 260)]:
        table.heading(name, text=label, anchor='w')
        table.column(name, width=width, anchor='w')
    table.grid(row=2, column=0, sticky='nsew', pady=12)
    values = {r['index']: r['value'] for r in entry['plan'] if r['allowed']}
    for row in entry['plan']:
        table.insert('', 'end', iid=str(row['index']), values=(row['title'], row['value'], row['status']))
    edit_hint = ttk.Label(frame, text='选中上方可填写的题目，在下方修改本次内容；清空表示跳过。', wraplength=830)
    edit_hint.grid(row=3, column=0, sticky='ew', pady=(0, 6))
    bind_wrap(frame, edit_hint, lambda event: max(100, event.width - 32))
    edit = tk.Text(frame, height=3, wrap='word', font=FONTS['body'], state='disabled')
    edit.grid(row=4, column=0, sticky='ew')
    chosen = [None]

    def flush():
        if chosen[0] in values:
            values[chosen[0]] = edit.get('1.0', 'end-1c')
            row = entry['plan'][chosen[0]]
            table.item(str(chosen[0]), values=(row['title'], values[chosen[0]], '待确认 / 空白则跳过'))

    def select(event):
        flush()
        ids = table.selection()
        chosen[0] = int(ids[0]) if ids else None
        edit.configure(state='normal')
        edit.delete('1.0', 'end')
        if chosen[0] in values:
            edit.insert('1.0', values[chosen[0]])
        else:
            edit.configure(state='disabled')

    table.bind('<<TreeviewSelect>>', select)

    def confirm():
        flush()
        with app.preview_lock:
            current = app.previews.get(url)
            if not current or current['token'] != entry['token'] or current['state'] != 'pending':
                feedback.set('预览已变化，请关闭后重新打开。', state='warning')
                messagebox.showinfo('预览已变化', '请关闭后重新打开最新预览。', parent=win)
                return
            if any(len(v) > 4000 for v in values.values()):
                feedback.set('内容过长，每项最多4000字。', state='error')
                messagebox.showerror('内容过长', '每项最多4000字。', parent=win)
                return
            current['approved'] = dict(values)
            current['state'] = 'approved'
        app.emit('已确认本次填写；执行前会再次核对网页。最终仍由你手动提交。', state='warning')
        win.destroy()

    ttk.Frame(frame, height=1, style='Divider.TFrame').grid(row=5, column=0, sticky='ew', pady=(16, 0))
    bar = ttk.Frame(frame)
    bar.grid(row=6, column=0, sticky='ew', pady=(12, 0))
    ttk.Button(bar, text='取消', command=win.destroy).pack(side='right')
    ttk.Button(
        bar,
        text='确认填写（不提交）',
        style='Primary.TButton',
        command=confirm,
        state='normal' if values else 'disabled',
    ).pack(side='right', padx=8)
    themed(app, win)
