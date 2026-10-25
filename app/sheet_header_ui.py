"""One compact, read-only header preview using the app's existing browser worker."""

import queue, threading, time
import tkinter as tk
from tkinter import ttk
from ui_layout import fit_window, bind_wrap
from ui_status import StatusVar, StatusLabel
from appearance import themed
from diagnostics import describe_error
from sheet_headers import parse_range, parse_pasted_region, analyze_headers
from sheet_rows import assess_candidate_row
from providers.tencent_sheet import is_tencent_sheet


def show_headers(app):
    selected = app.table.selection()
    if len(selected) != 1 or not is_tencent_sheet(selected[0]):
        app.status.set('请先选择一个腾讯在线表格任务。', state='warning')
        return
    existing = getattr(app, 'sheet_header_window', None)
    if existing and existing.winfo_exists():
        existing.lift()
        if getattr(app, 'sheet_header_url', None) != selected[0]:
            app.status.set('已打开另一项任务的表头预览，请先关闭该窗口，再预览当前任务。', state='warning')
        return
    url = selected[0]
    win = tk.Toplevel(app.root)
    app.sheet_header_window = win
    app.sheet_header_url = url
    win.title('腾讯表格 · 表头读取预览（实验性）')
    win.transient(app.root)
    fit_window(win, 900, 680, (600, 460))
    frame = ttk.Frame(win, padding=16)
    frame.pack(fill='both', expand=True)
    frame.columnconfigure(0, weight=1)
    frame.rowconfigure(5, weight=1)
    instruction = ttk.Label(
        frame,
        text='先读取范围、核对表头和空行候选；需要填写时打开填写预览并确认写入。在线表格可能即时保存，请勿同时编辑。',
        wraplength=820,
    )
    instruction.grid(row=0, column=0, sticky='ew')
    feedback = StatusVar(win, '打开助手浏览器并登录后读取；也可复制网页中的表头区域，再粘贴分析。', state='idle')
    feedback_label = StatusLabel(frame, feedback, wraplength=820)
    feedback_label.grid(row=1, column=0, sticky='ew', pady=(8, 8))
    bar = ttk.Frame(frame)
    bar.grid(row=2, column=0, sticky='ew')
    region = tk.StringVar(value='A1:H5')
    ttk.Label(bar, text='读取范围／粘贴起点').pack(side='left')
    entry = ttk.Entry(bar, textvariable=region, width=16)
    entry.pack(side='left', padx=8)
    result_meta = tk.StringVar(value='尚无读取结果')
    meta_label = ttk.Label(frame, textvariable=result_meta, style='Muted.TLabel', wraplength=820)
    meta_label.grid(row=3, column=0, sticky='ew', pady=8)
    select_bar = ttk.Frame(frame)
    select_bar.grid(row=4, column=0, sticky='ew')
    chosen = tk.StringVar()
    ttk.Label(select_bar, text='确认表头行').pack(side='left')
    selector = ttk.Combobox(select_bar, textvariable=chosen, state='disabled', width=14)
    selector.pack(side='left', padx=8)
    check_button = ttk.Button(select_bar, text='确认表头并检查空行', state='disabled')
    check_button.pack(side='left')
    preview_button = ttk.Button(select_bar, text='填写预览', state='disabled')
    preview_button.pack(side='left', padx=8)
    listing = ttk.Frame(frame)
    listing.grid(row=5, column=0, sticky='nsew', pady=10)
    listing.columnconfigure(0, weight=1)
    listing.rowconfigure(0, weight=1)
    table = ttk.Treeview(listing, columns=('address', 'title', 'key', 'state'), show='headings', height=10)
    for name, title, width in [
        ('address', '单元格', 85),
        ('title', '表头原文', 230),
        ('key', '匹配资料', 150),
        ('state', '状态', 330),
    ]:
        table.heading(name, text=title, anchor='w')
        table.column(name, width=width, minwidth=60)
    table.grid(row=0, column=0, sticky='nsew')
    scroll = ttk.Scrollbar(listing, orient='vertical', command=table.yview)
    scroll.grid(row=0, column=1, sticky='ns')
    table.configure(yscrollcommand=scroll.set)
    horizontal = ttk.Scrollbar(listing, orient='horizontal', command=table.xview)
    horizontal.grid(row=1, column=0, sticky='ew')
    table.configure(xscrollcommand=horizontal.set)
    row_feedback = StatusVar(win, '尚未检查空行候选。', state='idle')
    row_label = StatusLabel(frame, row_feedback, wraplength=820)
    row_label.grid(row=6, column=0, sticky='ew', pady=(0, 8))
    for label in (instruction, feedback_label, meta_label, row_label):
        bind_wrap(frame, label, lambda event: max(1, event.width - 32))
    snapshot = [None]
    region_data = [None]
    profile_snapshot = [None]
    request = [None]
    timer = [None]
    scope_revision = [0]

    def clear():
        snapshot[0] = None
        region_data[0] = None
        profile_snapshot[0] = None
        chosen.set('')
        selector.configure(values=(), state='disabled')
        check_button.configure(state='disabled')
        preview_button.configure(state='disabled')
        row_feedback.set('尚未检查空行候选。', state='idle')
        table.delete(*table.get_children())
        result_meta.set('尚无读取结果')

    def range_changed(*_):
        scope_revision[0] += 1
        if request[0]:
            request[0]['cancel'].set()
        clear()
        feedback.set('读取范围已变化，请重新读取或粘贴分析。', state='warning')

    range_trace = region.trace_add('write', range_changed)

    def display_row(*_):
        check_button.configure(state='disabled')
        preview_button.configure(state='disabled')
        row_feedback.set('确认所选表头后，可检查范围内空行候选。', state='idle')
        table.delete(*table.get_children())
        if not snapshot[0] or not chosen.get():
            return
        row = next((r for r in snapshot[0]['rows'] if f"第{r['row']}行" == chosen.get()), None)
        if not row:
            return
        check_button.configure(state='normal')
        for column in row['columns']:
            key = (column['key'] or '').removeprefix('custom:')
            table.insert('', 'end', values=(column['address'], column['title'], key, column['state']))

    selector.bind('<<ComboboxSelected>>', display_row)

    def check_row():
        try:
            row = (
                next((r for r in snapshot[0]['rows'] if f"第{r['row']}行" == chosen.get()), None)
                if snapshot[0]
                else None
            )
            if not row or not region_data[0]:
                raise ValueError('请先读取范围并选择表头行。')
            report = assess_candidate_row(
                region_data[0]['bounds'], region_data[0]['matrix'], profile_snapshot[0], row['row']
            )
            row_feedback.set(report['message'], state=report['status'])
            preview_button.configure(state='normal' if report['candidate_row'] else 'disabled')
        except Exception as error:
            row_feedback.set(describe_error(error), state='error')

    check_button.configure(command=check_row)

    def show_plan():
        if not region_data[0] or not chosen.get():
            return
        from sheet_fill_ui import show_sheet_fill

        data = region_data[0]
        profile = profile_snapshot[0]
        selected_row = chosen.get()
        selected_scope = scope_revision[0]
        row = next(r['row'] for r in snapshot[0]['rows'] if f"第{r['row']}行" == selected_row)
        show_sheet_fill(
            app,
            url,
            data,
            profile,
            row,
            valid=lambda: (
                win.winfo_exists()
                and region_data[0] is data
                and chosen.get() == selected_row
                and scope_revision[0] == selected_scope
            ),
            on_dispatch=clear,
        )

    preview_button.configure(command=show_plan)

    def publish(data, profile):
        analysis = analyze_headers(data['bounds'], data['matrix'], profile)
        snapshot[0] = analysis
        region_data[0] = data
        profile_snapshot[0] = profile
        selector.configure(values=tuple(f"第{r['row']}行" for r in analysis['rows']), state='readonly')
        row = analysis['suggested_row']
        chosen.set(f'第{row}行' if row else '')
        display_row()
        result_meta.set(
            f"{data['document_title']} · {data['worksheet']} · {analysis['range']} · {data['source']}\n候选表头行："
            + (', '.join(map(str, analysis['candidates'])) or '未自动确认')
            + '；结果仅保存在本窗口。'
        )
        feedback.set(
            '所读范围为空，请核对范围与工作表。'
            if analysis['status'] == 'empty'
            else '发现一个候选表头行，请核对列名。'
            if row
            else '表头候选不唯一或未匹配，请在上方手动选择表头行。',
            state=analysis['status'],
        )

    def finish():
        request[0] = None
        read_button.configure(state='normal')
        entry.configure(state='normal')
        cancel_button.configure(state='disabled')

    def poll():
        timer[0] = None
        if not win.winfo_exists() or request[0] is None:
            return
        req = request[0]
        try:
            data = req['reply'].get_nowait()
        except queue.Empty:
            if time.monotonic() - req['created'] >= 120:
                req['cancel'].set()
                finish()
                feedback.set('等待读取超时，请检查助手浏览器或粘贴范围分析。', state='error')
                return
            timer[0] = win.after(100, poll)
            return
        finish()
        if req['scope_revision'] != scope_revision[0]:
            clear()
            feedback.set('读取范围已变化，本次结果已丢弃，请重新读取。', state='warning')
            return
        if req['cancel'].is_set():
            clear()
            feedback.set('读取已取消，本次结果已丢弃。', state='cancelled')
            return
        if data.get('error'):
            feedback.set(data['error'], state='error')
            return
        try:
            publish(data, req['profile'])
        except Exception as error:
            clear()
            feedback.set(describe_error(error), state='error')

    def read():
        if request[0] is not None:
            return
        clear()
        try:
            bounds = parse_range(region.get())
            if not app.processing.is_set():
                raise ValueError('请先在主窗口继续任务处理，再读取助手浏览器中的表格。')
            if not app.browser_thread or not app.browser_thread.is_alive():
                raise ValueError('请先在任务清单打开表格，并在助手浏览器登录。')
            req = {
                'url': url,
                'range': bounds.address,
                'profile': app.get_config(),
                'cancel': threading.Event(),
                'reply': queue.Queue(maxsize=1),
                'created': time.monotonic(),
                'scope_revision': scope_revision[0],
            }
            app.sheet_requests.put_nowait(req)
            request[0] = req
            read_button.configure(state='disabled')
            entry.configure(state='disabled')
            cancel_button.configure(state='normal')
            feedback.set('正在读取所选工作表，请勿同时编辑或切换工作表。', state='running')
            timer[0] = win.after(100, poll)
        except queue.Full:
            feedback.set('已有读取请求等待执行，请稍候再试。', state='warning')
        except Exception as error:
            feedback.set(describe_error(error), state='error')

    def cancel():
        if request[0]:
            request[0]['cancel'].set()
        feedback.set('已请求取消，等待当前单元格读取结束。', state='warning')

    read_button = ttk.Button(bar, text='读取网页表头', style='Primary.TButton', command=read)
    read_button.pack(side='left')
    cancel_button = ttk.Button(bar, text='取消读取', command=cancel, state='disabled')
    cancel_button.pack(side='left', padx=8)
    bottom = ttk.Frame(frame)
    bottom.grid(row=7, column=0, sticky='ew')

    def paste():
        if request[0] is not None:
            feedback.set('请先取消并等待网页读取结束，再粘贴分析。', state='warning')
            return
        clear()
        try:
            first = parse_range(region.get())
            bounds, matrix = parse_pasted_region(
                win.clipboard_get(), first.address.split(':')[0], require_rectangular=True
            )
            publish(
                {
                    'bounds': bounds,
                    'matrix': matrix,
                    'document_title': '手动复制内容',
                    'worksheet': '请在网页核对工作表',
                    'source': '剪贴板范围（未验证来源）',
                },
                app.get_config(),
            )
        except tk.TclError:
            feedback.set('剪贴板没有可分析的文字，请先复制矩形单元格区域。', state='empty')
        except Exception as error:
            feedback.set(describe_error(error), state='error')

    ttk.Button(bottom, text='粘贴范围分析', command=paste).pack(side='left')
    ttk.Button(bottom, text='设置资料', command=app.open_settings).pack(side='left', padx=8)
    ttk.Button(bottom, text='关闭', command=win.destroy).pack(side='right')

    def destroyed(event):
        if event.widget is not win:
            return
        try:
            region.trace_remove('write', range_trace)
        except tk.TclError:
            pass
        if request[0]:
            request[0]['cancel'].set()
        if timer[0]:
            try:
                win.after_cancel(timer[0])
            except tk.TclError:
                pass
        snapshot[0] = None
        region_data[0] = None
        profile_snapshot[0] = None

    win.bind('<Destroy>', destroyed, add='+')
    themed(app, win)
