"""Explicit, preview-first cleanup of selected local data categories."""

from design_tokens import FONTS
import tkinter as tk
from tkinter import ttk, messagebox
from appearance import themed
from ui_layout import fit_window
from ui_status import StatusVar, StatusLabel
from about_ui import text_panel, set_content
from data_cleanup import CleanupOptions, preview_cleanup, execute_cleanup, describe_plan


def show_cleanup(app):
    previous = getattr(app, 'cleanup_window', None)
    if previous and previous.winfo_exists():
        previous.lift()
        return
    win = tk.Toplevel(app.root)
    app.cleanup_window = win
    win.title('数据清理中心')
    win.transient(app.root)
    fit_window(win, 880, 740, (640, 520))
    bottom = ttk.Frame(win, padding=16)
    bottom.pack(side='bottom', fill='x')
    ttk.Button(bottom, text='关闭', command=win.destroy).pack(side='right')
    body = ttk.Frame(win, padding=16)
    body.pack(fill='both', expand=True)
    ttk.Label(body, text='先预览，再清理', font=FONTS['section']).pack(anchor='w')
    feedback = StatusVar(win, '请选择清理项目，然后生成预览。', state='idle')
    StatusLabel(body, feedback, wraplength=760).pack(fill='x', pady=(8, 0))
    ttk.Label(
        body,
        text='个人资料、浏览器登录数据、未忽略的待办及暂缓任务、未读提醒、手动备份和恢复前副本会保留。\n任务清理会解除图片关联，图片仍保留，可从“待关联图片”单独删除。\n删除的链接再次出现在群聊通知中时可能重新加入；不想再处理时可继续保留为“已忽略”。',
        wraplength=800,
    ).pack(anchor='w', pady=8)
    choices = ttk.Frame(body)
    choices.pack(fill='x')
    days = tk.StringVar(value='30')
    options = {key: tk.BooleanVar(value=False) for key in ('tasks', 'alerts', 'backups', 'logs')}
    safety = tk.BooleanVar(value=True)
    ttk.Label(choices, text='仅清理早于').pack(side='left')
    ttk.Combobox(choices, textvariable=days, values=['7', '30', '90', '180'], state='readonly', width=6).pack(
        side='left', padx=8
    )
    ttk.Label(choices, text='天的记录或文件（任务按最后修改时间）').pack(side='left')
    for key, label in [
        ('tasks', '已处理 / 已忽略任务及关联原通知'),
        ('alerts', '已读提醒'),
        ('backups', '旧自动备份（始终保留最近2份）'),
        ('logs', '旧轮转日志（保留正在写入的日志）'),
    ]:
        ttk.Checkbutton(body, text=label, variable=options[key]).pack(anchor='w', pady=2)
    ttk.Checkbutton(body, text='删除任务或提醒前创建安全备份（备份仍含原数据）', variable=safety).pack(
        anchor='w', pady=(8, 4)
    )
    panel, text = text_panel(body)
    panel.pack(fill='both', expand=True, pady=8)
    set_content(
        text,
        '请选择清理项目并生成预览。默认不选择任何删除项目。\n\n这里不会清空个人资料或浏览器登录状态，也不处理系统临时目录和外部导出文件。完整删除方式见“关于与软件声明 → 隐私说明”。',
    )
    snapshot = [None]
    generation = [0]

    def read_options():
        return CleanupOptions(int(days.get()), **{k: v.get() for k, v in options.items()}, safety_backup=safety.get())

    def invalidate(*_):
        generation[0] += 1
        snapshot[0] = None
        confirm.configure(state='disabled')
        feedback.set('选项已改变，请重新预览。', state='warning')
        set_content(text, '选项已改变，请重新生成预览。当前没有新的删除授权。')

    for variable in [days, safety, *options.values()]:
        variable.trace_add('write', invalidate)

    def ready():
        if not app.can_start_file_job():
            return False
        if app.processing.is_set() or any(t and t.is_alive() for t in (app.monitor_thread, app.browser_thread)):
            messagebox.showinfo(
                '请先停止活动操作',
                '请停止群聊监测、暂停任务处理，并手动关闭助手打开的浏览器窗口，再生成清理预览。清理不会强制关闭未提交表单。',
                parent=win,
            )
            return False
        if (app.data / 'restore_pending.zip').exists() or (app.data / 'restore_journal.json').exists():
            messagebox.showinfo('请先完成恢复', '当前存在待恢复数据，请完成恢复后再清理。', parent=win)
            return False
        return True

    def analyse():
        if not ready():
            return
        invalidate()
        token = generation[0]
        selected = read_options()
        if not any((selected.tasks, selected.alerts, selected.backups, selected.logs)):
            set_content(text, '尚未选择清理项目；没有数据会被删除。')
            feedback.set('尚未选择清理项目。', state='empty')
            return
        set_content(text, '正在检查记录与文件大小，请稍候…')

        def complete(plan):
            if not win.winfo_exists() or token != generation[0]:
                return
            snapshot[0] = plan
            set_content(text, describe_plan(plan))
            if plan.task_urls or plan.alert_ids or plan.files:
                confirm.configure(state='normal')
                feedback.set('请核对预览范围；确认后才会删除。', state='warning')
            else:
                feedback.set('没有符合当前条件的可清理数据。', state='empty')
                app.status.set('没有符合当前条件的可清理数据。', state='empty')

        app.run_file_job(
            '生成清理预览', lambda: preview_cleanup(app.store, app.data, selected), complete, feedback=feedback
        )

    def clean():
        plan = snapshot[0]
        if plan is None or not ready():
            return
        if plan.options != read_options():
            invalidate()
            return
        detail = f'删除 {len(plan.task_urls)} 项任务、{plan.notice_count} 条原通知、{len(plan.alert_ids)} 条提醒和 {len(plan.files)} 个文件。'
        detail += '\n' + (
            '删除任务/提醒前会创建安全备份。'
            if plan.options.safety_backup
            else '不创建安全备份，删除的记录无法通过此操作撤销。'
        )
        detail += (
            '\n文件删除不进入回收站。旧备份、其他提醒或外部导出文件中仍可能保留历史信息。\n\n确认执行本次预览范围？'
        )
        if not messagebox.askyesno('确认清理', detail, parent=win, default='no'):
            feedback.set('本次未删除任何数据。', state='cancelled')
            return
        config = app.get_config()
        invalidate()

        def complete(result):
            app.table_snapshot = None
            app.refresh_table()
            summary = f"已清理 {result['tasks']} 项任务、{result['notices']} 条原通知、{result['alerts']} 条提醒和 {len(result['files'])} 个文件。"
            if result['safety']:
                summary += '\n安全备份：' + result['safety']
            if result['failed']:
                summary += '\n以下文件被占用或发生变化，未删除，请重新预览：\n' + '\n'.join(result['failed'])
            if win.winfo_exists():
                set_content(text, summary)
            message = (
                '清理完成；部分文件未删除，请查看清理中心。'
                if result['failed']
                else '清理完成，所选范围以外的数据已保留。'
            )
            state = 'warning' if result['failed'] else 'success'
            feedback.set(message, state=state)
            app.status.set(message, state=state)

        app.run_file_job(
            '清理数据', lambda: execute_cleanup(app.store, app.data, config, plan), complete, feedback=feedback
        )

    ttk.Button(bottom, text='生成预览', command=analyse).pack(side='left')
    confirm = ttk.Button(bottom, text='确认清理…', style='Danger.TButton', command=clean, state='disabled')
    confirm.pack(side='left', padx=8)
    themed(app, win)
