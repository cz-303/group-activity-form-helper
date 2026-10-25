from ui_layout import fit_window, bind_wrap
from design_tokens import FONTS
from diagnostics import describe_error
import re, threading, queue, time
import tkinter as tk
from tkinter import ttk, messagebox
from core import capture_notifications, canonical_url
from task_links import canonical_task_url
from appearance import themed
from ui_status import StatusVar, StatusLabel


def parse_import(text, extra_link=''):
    if not text.strip():
        raise ValueError('请粘贴完整通知正文。')
    if len(text) > 500000:
        raise ValueError('一次最多导入50万字，请分批导入。')
    blocks = [b.strip() for b in re.split(r'(?m)^\s*---\s*$', text) if b.strip()]
    urls, notices = capture_notifications(blocks)
    if extra_link.strip():
        url = canonical_task_url(extra_link)
        if not url:
            raise ValueError('补充链接需为腾讯收集表、腾讯在线表格或金山文档完整链接。')
        if len(blocks) != 1:
            raise ValueError('填写补充链接时，请一次只补录一份通知，避免关联错误。')
        notices.append((url, blocks[0]))
        urls = sorted(set(urls + [url]))
    if not urls:
        raise ValueError('正文未找到支持的活动文档链接，可在补充链接中填写。')
    missing = sum(not capture_notifications([b])[0] for b in blocks) if not extra_link.strip() else 0
    return urls, list(dict.fromkeys(notices)), missing


def import_notice(app):
    win = tk.Toplevel(app.root)
    win.transient(app.root)
    win.title('补录 / 粘贴通知导入')
    fit_window(win, 850, 720)
    f = ttk.Frame(win, padding=(24, 20, 24, 16))
    f.pack(fill='both', expand=True)
    f.columnconfigure(0, weight=1)
    f.rowconfigure(3, weight=3)
    f.rowconfigure(7, weight=1)
    header = ttk.Frame(f)
    header.grid(row=0, column=0, sticky='ew')
    ttk.Label(header, text='导入通知', style='Dialog.TLabel').pack(anchor='w')
    hint = ttk.Label(
        header,
        text='粘贴完整通知。一份通知中的多个链接会关联同一原文；多份通知请用独立一行 --- 分隔。',
        wraplength=790,
        style='Muted.TLabel',
    )
    hint.pack(anchor='w', fill='x', pady=(4, 0))
    bind_wrap(f, hint, lambda event: max(100, event.width - 48))
    feedback = StatusVar(win, '粘贴通知后分析预览，再确认导入。', state='idle')
    status = StatusLabel(f, feedback, wraplength=760)
    status.grid(row=1, column=0, sticky='ew', pady=(8, 0))
    bind_wrap(f, status, lambda event: max(100, event.width - 32))
    source = tk.StringVar(value='手动补录')
    extra = tk.StringVar()
    row = ttk.Frame(f)
    row.grid(row=2, column=0, sticky='ew', pady=8)
    ttk.Label(row, text='来源', style='Muted.TLabel').pack(side='left')
    ttk.Entry(row, textvariable=source).pack(side='left', fill='x', expand=True, padx=8)
    text = tk.Text(f, height=12, wrap='word', font=FONTS['body'])
    text.grid(row=3, column=0, sticky='nsew')

    def paste():
        try:
            text.insert('insert', win.clipboard_get())
        except tk.TclError:
            messagebox.showinfo('剪贴板为空', '剪贴板没有可粘贴的文字。', parent=win)

    ttk.Button(f, text='粘贴剪贴板文字', command=paste).grid(row=4, column=0, sticky='e', pady=5)
    extra_hint = ttk.Label(f, text='补充链接（正文未含链接时可填；只关联一份通知）', wraplength=790, style='Muted.TLabel')
    extra_hint.grid(row=5, column=0, sticky='ew')
    bind_wrap(f, extra_hint, lambda event: max(100, event.width - 32))
    ttk.Entry(f, textvariable=extra).grid(row=6, column=0, sticky='ew', pady=(3, 8))
    result = tk.Text(f, height=6, wrap='word', state='disabled', font=FONTS['body'])
    result.grid(row=7, column=0, sticky='nsew')
    snapshot = [None]

    def values():
        return text.get('1.0', 'end-1c'), extra.get(), source.get().strip() or '手动补录'

    def invalidate(*_):
        if snapshot[0] is not None:
            snapshot[0] = None
            feedback.set('内容已改变，请重新分析预览。', state='warning')
        confirm.configure(state='disabled')

    def edited(event):
        if text.edit_modified():
            invalidate()
            text.edit_modified(False)

    text.bind('<<Modified>>', edited)
    extra.trace_add('write', invalidate)
    source.trace_add('write', invalidate)

    def analyse():
        snapshot[0] = None
        confirm.configure(state='disabled')
        try:
            body, link, src = values()
            urls, notices, missing = parse_import(body, link)
            counts = app.store.preview_additions(urls)
            snapshot[0] = (values(), urls, notices)
            result.configure(state='normal')
            result.delete('1.0', 'end')
            result.insert(
                '1.0',
                f"识别 {counts['links']} 个链接：预计新增 {counts['new']} 项，其余 {counts['merged']} 个链接合并保存。\n监测可能同时捕获；确认时以实际保存结果为准。\n"
                + (f'注意：{missing} 份通知无可识别链接，将不导入。\n' if missing else '')
                + '\n'.join(urls),
            )
            result.configure(state='disabled')
            text.edit_modified(False)
            confirm.configure(state='normal')
            feedback.set(
                '部分通知未识别，请核对预览。' if missing else '预览已生成，请核对后确认导入。',
                state='warning' if missing else 'success',
            )
        except Exception as e:
            feedback.set(describe_error(e), state='error')
            messagebox.showerror('无法解析', describe_error(e), parent=win)

    def save():
        if not snapshot[0] or snapshot[0][0] != values():
            messagebox.showinfo('先预览', '请先点击“分析预览”；修改文字后需重新分析。', parent=win)
            return
        try:
            inputs, urls, notices = snapshot[0]
            n = app.store.add_many(urls, inputs[2], notices)
            app.emit(f'通知补录完成：新增 {n} 项，重复链接已合并；已处理/忽略的任务不会重新排队。', state='success')
            app.refresh_table()
            win.destroy()
        except Exception as e:
            feedback.set(describe_error(e), state='error')
            messagebox.showerror('导入失败', describe_error(e), parent=win)

    ttk.Frame(f, height=1, style='Divider.TFrame').grid(row=8, column=0, sticky='ew', pady=(16, 0))
    bar = ttk.Frame(f)
    bar.grid(row=9, column=0, sticky='ew', pady=(12, 0))
    ttk.Button(bar, text='分析预览', command=analyse).pack(side='left')
    confirm = ttk.Button(bar, text='确认导入', style='Primary.TButton', command=save, state='disabled')
    confirm.pack(side='right')
    themed(app, win)


def capture_diagnostic(app):
    if getattr(app, 'diagnostic_window', None) and app.diagnostic_window.winfo_exists():
        app.diagnostic_window.lift()
        return
    win = tk.Toplevel(app.root)
    win.transient(app.root)
    app.diagnostic_window = win
    win.title('捕获诊断与自检')
    fit_window(win, 800, 600)
    f = ttk.Frame(win, padding=16)
    f.pack(fill='both', expand=True)
    ttk.Label(f, text='只读检查当前聊天窗口、目标群及可读取消息，不会切群、发送消息或自动导入。', wraplength=740).pack(
        anchor='w'
    )
    feedback = StatusVar(win, '准备好聊天窗口后，开始检查。', state='idle')
    StatusLabel(f, feedback, wraplength=720).pack(fill='x', pady=(8, 0))
    text = tk.Text(f, wrap='word', font=FONTS['body'], state='disabled')
    text.pack(fill='both', expand=True, pady=12)
    results = queue.Queue()
    captured = [None]
    worker = [None]
    generation = [0]
    poll_timer = [None]

    def cancel_poll(event):
        if event.widget is win and poll_timer[0]:
            try:
                win.after_cancel(poll_timer[0])
            except tk.TclError:
                pass
            poll_timer[0] = None

    win.bind('<Destroy>', cancel_poll, add='+')

    def show(value):
        text.configure(state='normal')
        text.delete('1.0', 'end')
        text.insert('1.0', value)
        text.configure(state='disabled')

    def run():
        if worker[0] and worker[0].is_alive():
            return
        config = app.get_config()
        captured[0] = None
        generation[0] += 1
        token = generation[0]
        show('正在检查，请保持聊天客户端展开并停留在目标群……')
        feedback.set('正在读取当前目标群，请稍候。', state='running')
        check_button.configure(state='disabled')
        import_button.configure(state='disabled')

        def work():
            try:
                import uiautomation as auto
                from app import read_group
                from adapters.chat_client import client_process, client_windows

                with auto.UIAutomationInitializerInThread():
                    windows = client_windows(auto, client_process(config))
                    prefix = f"检查时间：{time.strftime('%Y-%m-%d %H:%M:%S')}\n目标群：{config['group']}\n客户端顶层窗口：{len(windows)} 个\n"
                    texts = read_group(auto, config['group'], client_process(config))
                urls, notices = capture_notifications(texts)
                counts = app.store.preview_additions(urls)
                body = (
                    prefix
                    + f"目标群及消息容器：已确认\n可读取消息：{len(texts)} 条\n活动文档链接：{len(urls)} 个（尚未保存 {counts['new']} 项，其余 {counts['merged']} 个链接将合并）\n\n"
                )
                body += (
                    '\n'.join(urls[:20])
                    if urls
                    else '没有找到支持的文字链接。可能尚未加载对应消息，或通知只含图片/二维码。'
                )
                body += '\n\n支持腾讯收集表、腾讯在线表格和金山文档分享文字链接。小程序卡片尚不能自动展开；可打开后复制浏览器分享链接导入。\n诊断成功只表示本次读取成功，不能证明历史消息均已捕获。'
                results.put((token, body, (config['group'], urls, notices)))
            except Exception as e:
                results.put(
                    (
                        token,
                        '检查未通过：'
                        + describe_error(e)
                        + '\n\n请检查：\n1. 设置中是否已选择聊天客户端程序，客户端是否已打开并展开；\n2. 当前顶部群名是否与设置一致；\n3. 是否启用客户端辅助模式；\n4. 是否同时打开多个目标群窗口。\n\n仍无法读取时，可使用“补录 / 粘贴通知导入”。',
                        None,
                    )
                )

        worker[0] = threading.Thread(target=work, daemon=True)
        worker[0].start()
        poll_timer[0] = win.after(150, poll)

    def poll():
        if not win.winfo_exists():
            return
        try:
            token, body, data = results.get_nowait()
            if token == generation[0]:
                show(body)
                captured[0] = data
                check_button.configure(state='normal')
                if data is None:
                    feedback.set('本次检查未通过，请按下方提示重试。', state='error')
                elif not data[1]:
                    feedback.set('本次读取成功，没有识别到支持的链接。', state='empty')
                else:
                    feedback.set('检查完成，可核对后导入本次通知。', state='success')
                    import_button.configure(state='normal')
        except queue.Empty:
            poll_timer[0] = win.after(200, poll)

    def save():
        if not captured[0]:
            messagebox.showinfo('没有可导入的结果', '请先完成一次成功的检查。', parent=win)
            return
        source, urls, notices = captured[0]
        try:
            n = app.store.add_many(urls, source, notices)
            app.refresh_table()
            app.emit(f'已导入诊断快照：新增 {n} 项，重复链接已合并。', state='success')
            feedback.set(f'已导入本次通知，新增 {n} 项。', state='success')
            import_button.configure(state='disabled')
        except Exception as e:
            feedback.set(describe_error(e), state='error')
            messagebox.showerror('导入失败', describe_error(e), parent=win)

    bar = ttk.Frame(f)
    bar.pack(fill='x')
    check_button = ttk.Button(bar, text='开始 / 重新检查', command=run)
    check_button.pack(side='left')
    import_button = ttk.Button(bar, text='导入本次识别通知', style='Primary.TButton', command=save, state='disabled')
    import_button.pack(side='right')
    themed(app, win)
