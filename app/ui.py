"""Compact main window, task details and non-blocking local reminders."""

from ui_status import StatusVar, StatusLabel
from version import window_title
from design_tokens import FONTS, dp
from cleanup_ui import show_cleanup
from data_purge import request_purge
from about_ui import show_about
from ui_layout import fit_window, ResizeUpdate, bind_wrap, filter_inline, set_wrap, ListDetailLayout, work_area
import time
import tkinter as tk
from tkinter import ttk, messagebox
from activity_time import TIME_FILTERS, SORTS
from appearance import themed
from management_ui import show_alerts
from notice_tools import import_notice, capture_diagnostic
from fill_preview import show_fill_preview
from activity_editor import edit_activity
from migration_ui import show_migration
from attachment_ui import show_images
from detail_header_ui import SummaryLabel, set_header, show_full_detail


def build_main(app, root):
    """Build the main window: grey shell, sidebar, two status cards and one task card."""
    outer, status_row = _build_shell(app, root)
    card, monitor_label, summary_label, filters, filter_options, tools, reset_filters = _build_status_and_filters(
        app, root, outer, status_row
    )
    content_area, right, navigate_list = _build_task_list(app, root, card, reset_filters)
    _build_detail_panel(app, root, right, navigate_list)
    status_label = _build_task_status(app, root, outer, status_row)
    _bind_responsive_layout(
        app, root, outer, card, summary_label, monitor_label, status_label, filters, filter_options, tools
    )
    app.log_lines = []
    app.unread_new = 0
    app.reminder = None
    app.reminder_timer = None


def _nav_button(parent, text, command, active=False):
    button = ttk.Button(
        parent, text=text, style='NavActive.TButton' if active else 'Sidebar.TButton', command=command, takefocus=True
    )
    button.pack(fill='x', pady=1)
    return button


def _build_shell(app, root):
    """Window title/size, sidebar navigation and the status-card row (no page header: height goes to the list)."""
    root.title(window_title())
    fit_window(root, 1180, 780, (900, 620))
    shell = ttk.Frame(root, style='Shell.TFrame')
    shell.pack(fill='both', expand=True)
    sidebar = ttk.Frame(shell, padding=(8, 12, 8, 12), style='Sidebar.TFrame', width=dp(root, 184))
    sidebar.pack(side='left', fill='y')
    sidebar.pack_propagate(False)
    app.sidebar = sidebar
    ttk.Label(sidebar, text='活动助手', font=FONTS['section'], style='Sidebar.TLabel').pack(
        anchor='w', padx=12, pady=(4, 2)
    )
    ttk.Label(sidebar, text='群通知 · 非官方工具', font=FONTS['caption'], style='SidebarMuted.TLabel').pack(
        anchor='w', padx=12, pady=(0, 16)
    )

    def show_tasks():
        root.deiconify()
        if getattr(app, 'list_detail', None) is not None and app.list_detail.wide is False:
            app.list_detail.show_list()
        app.table.focus_set()

    _nav_button(sidebar, '任务清单', show_tasks, active=True)
    app.alert_button = _nav_button(sidebar, '提醒中心', lambda: show_alerts(app))
    _nav_button(sidebar, '待关联图片', lambda: show_images(app))
    ttk.Label(sidebar, text='控制', style='SidebarHeading.TLabel').pack(anchor='w', padx=12, pady=(16, 4))
    for label, items in [
        (
            '监测',
            [
                ('开始监测', app.start_monitor),
                ('停止监测', app.stop_monitor),
                ('捕获诊断与自检', lambda: capture_diagnostic(app)),
            ],
        ),
        ('处理', [('继续处理', app.resume), ('暂停处理', app.pause)]),
        (
            '更多',
            [
                ('补录 / 粘贴通知导入', lambda: import_notice(app)),
                ('待关联图片', lambda: show_images(app)),
                ('升级与数据迁移', lambda: show_migration(app)),
                ('数据清理中心', lambda: show_cleanup(app)),
                ('清除全部数据并退出…', lambda: request_purge(app)),
                ('最小化到托盘', app.hide_to_tray),
                ('导出任务清单', app.export),
                ('查看运行记录', lambda: show_logs(app)),
                ('关于与软件声明', lambda: show_about(app)),
            ],
        ),
    ]:
        button = ttk.Menubutton(sidebar, text=label, style='Sidebar.TMenubutton')
        menu = tk.Menu(button, tearoff=False)
        for title, command in items:
            menu.add_command(label=title, command=command)
        button.configure(menu=menu)
        button.pack(fill='x', pady=1)
    ttk.Button(sidebar, text='关于', style='Sidebar.TButton', command=lambda: show_about(app)).pack(
        side='bottom', fill='x', pady=1
    )
    ttk.Button(sidebar, text='设置', style='Sidebar.TButton', command=app.open_settings).pack(
        side='bottom', fill='x', pady=1
    )
    outer = ttk.Frame(shell, padding=(8, 12, 16, 12), style='Shell.TFrame')
    outer.pack(side='left', fill='both', expand=True)
    status_row = ttk.Frame(outer, style='Shell.TFrame')
    status_row.pack(fill='x', pady=(0, 12))
    status_row.columnconfigure(0, weight=1, uniform='status')
    status_row.columnconfigure(1, weight=1, uniform='status')
    return outer, status_row


def _status_card(parent, text, column):
    card = ttk.LabelFrame(parent, text=text, style='Card.TLabelframe', padding=(16, 12))
    card.grid(row=0, column=column, sticky='nsew', padx=(0, 6) if column == 0 else (6, 0))
    return card


def _build_status_and_filters(app, root, outer, status_row):
    """Chat monitor card, then the task card with its summary, search, query status and filter rows."""
    monitor_box = _status_card(status_row, '群聊监测', 0)
    app.monitor_button = ttk.Button(monitor_box, text='开始监测', command=app.start_monitor)
    app.monitor_button.pack(side='right', anchor='n', padx=(12, 0))
    app.monitor_summary = StatusVar(root, value='监测已停止', state='idle')
    monitor_label = StatusLabel(monitor_box, app.monitor_summary, wraplength=480)
    monitor_label.pack(anchor='w')
    bind_wrap(
        monitor_box,
        monitor_label,
        lambda e: max(120, e.width - dp(root, 44) - app.monitor_button.winfo_reqwidth()),
    )
    app.monitor_message = '尚未开始监测'
    app.monitor_last_success = None
    card = ttk.Frame(outer, style='Card.TFrame', padding=(16, 12, 16, 12))
    card.pack(fill='both', expand=True)
    app.task_card = card
    app.summary = tk.StringVar()
    summary_label = ttk.Label(card, textvariable=app.summary, wraplength=1180, style='Caption.TLabel')
    summary_label.pack(anchor='w', pady=(0, 8))
    filters = ttk.Frame(card)
    filters.pack(fill='x', pady=(0, 8))
    ttk.Label(filters, text='搜索', style='Muted.TLabel').pack(side='left', padx=(0, 8))
    app.search_var = tk.StringVar()
    ttk.Entry(filters, textvariable=app.search_var, width=16).pack(side='left', fill='x', expand=True)
    app.search_timer = None

    def search_changed(*args):
        app.query.invalidate()
        if app.search_timer:
            root.after_cancel(app.search_timer)

        def run():
            app.search_timer = None
            app.refresh_table()

        app.search_timer = root.after(250, run)

    app.search_var.trace_add('write', search_changed)
    query_row = ttk.Frame(card)
    app.query_row = query_row
    app.query_status = StatusVar(
        root,
    )
    query_label = StatusLabel(query_row, app.query_status, wraplength=850)
    query_label.pack(side='left', fill='x', expand=True)
    bind_wrap(query_row, query_label, lambda e: max(160, e.width - 130))
    app.query_retry_button = ttk.Button(query_row, text='重试查询', state='disabled', command=app.retry_query)
    app.query_retry_button.pack(side='right')
    app.source_var = tk.StringVar(value='全部来源')
    app.time_filter_var = tk.StringVar(value='全部时间')
    app.sort_var = tk.StringVar(value=SORTS[0])
    filter_options = ttk.Frame(card)
    for variable, values, width in [
        (app.source_var, ['全部来源'], 11),
        (app.time_filter_var, TIME_FILTERS, 10),
        (app.sort_var, SORTS, 11),
    ]:
        box = ttk.Combobox(filter_options, textvariable=variable, values=values, state='readonly', width=width)
        box.pack(side='left', padx=(8, 0))
        box.bind('<<ComboboxSelected>>', lambda e: app.refresh_table())
        if variable is app.source_var:
            app.source_chooser = box

    def reset_filters():
        app.search_var.set('')
        app.filter_var.set('进行中')
        app.source_var.set('全部来源')
        app.time_filter_var.set('全部时间')
        app.sort_var.set(SORTS[0])
        app.refresh_table()

    ttk.Button(filter_options, text='重置', width=5, command=reset_filters).pack(side='left', padx=(8, 0))
    tools = ttk.Frame(card)
    tools.pack(fill='x', pady=(0, 12))
    app.query_row_before = tools
    ttk.Label(tools, text='查看', style='Muted.TLabel').pack(side='left')
    app.filter_var = tk.StringVar(value='进行中')
    chooser = ttk.Combobox(
        tools,
        textvariable=app.filter_var,
        values=['进行中', '全部', '通知有更新', '待处理', '打开中', '已打开', '打开失败', '已暂缓', '已忽略', '已处理'],
        state='readonly',
        width=10,
    )
    chooser.pack(side='left', padx=(8, 16))
    chooser.bind('<<ComboboxSelected>>', lambda e: app.refresh_table())
    ttk.Label(tools, text='添加链接', style='Muted.TLabel').pack(side='left', padx=(0, 8))
    app.url_var = tk.StringVar()
    ttk.Entry(tools, textvariable=app.url_var).pack(side='left', expand=True, fill='x')
    ttk.Button(tools, text='加入清单', command=app.manual_add).pack(side='left', padx=(8, 0))
    ttk.Button(tools, text='＋ 导入通知', command=lambda: import_notice(app)).pack(side='left', padx=(8, 0))
    return card, monitor_label, summary_label, filters, filter_options, tools, reset_filters


def _build_task_list(app, root, card, reset_filters):
    """Task table, shared action bar, task menu, list/detail navigation and empty state."""
    content_area = ttk.Frame(card)
    content_area.pack(fill='both', expand=True)
    panes = ttk.Panedwindow(content_area, orient='horizontal')
    panes.pack(fill='both', expand=True)
    left = ttk.Frame(panes)
    right = ttk.Frame(panes, padding=(8, 0, 0, 0))
    panes.add(left, weight=3)
    panes.add(right, weight=2)
    app.table = ttk.Treeview(
        left, columns=('title', 'state', 'fields', 'deadline'), show='headings', selectmode='extended', height=10
    )
    for key, title, width in [
        ('title', '活动 / 表单', 200),
        ('state', '状态', 110),
        ('fields', '待补填', 100),
        ('deadline', '距离截止', 170),
    ]:
        app.table.heading(key, text=title, anchor='w')
        app.table.column(key, width=width, minwidth=80, stretch=key != 'state', anchor='w')
    scroll = ttk.Scrollbar(left, orient='vertical', command=app.table.yview)
    app.table.configure(yscrollcommand=scroll.set)
    app.table.grid(row=0, column=0, sticky='nsew')
    scroll.grid(row=0, column=1, sticky='ns')
    left.rowconfigure(0, weight=1)
    left.columnconfigure(0, weight=1)
    bottom = ttk.Frame(content_area)
    bottom.pack(side='bottom', before=panes, fill='x', pady=(12, 0))
    ttk.Frame(content_area, height=1, style='Divider.TFrame').pack(side='bottom', before=panes, fill='x', pady=(12, 0))
    app.open_button = ttk.Button(bottom, text='打开网页', command=app.requeue_selected, state='disabled')
    app.open_button.pack(side='left')
    app.task_menu = tk.Menu(root, tearoff=False)
    app.task_menu.add_command(label='填写前预览', command=lambda: show_fill_preview(app))
    app.task_menu.add_command(
        label='活动图片与群二维码', command=lambda: show_images(app, next(iter(app.table.selection()), None))
    )
    app.task_menu.add_command(label='备注 / 准备清单 / 专属提醒', command=lambda: edit_activity(app))
    app.task_menu.add_command(label='编辑活动时间 / 截止时间', command=lambda: edit_dates(app))
    app.task_menu.add_command(label='标记已处理', command=app.done_selected)
    snooze = tk.Menu(app.task_menu, tearoff=False)
    for label, seconds in [('10分钟', 600), ('1小时', 3600), ('明天此时', 86400)]:
        snooze.add_command(label=label, command=lambda s=seconds: app.defer_selected(s))
    app.task_menu.add_cascade(label='暂缓', menu=snooze)
    app.task_menu.add_command(label='忽略任务', command=app.ignore_selected)
    app.task_menu.add_command(label='恢复原状态', command=app.restore_selected)
    app.task_menu.add_separator()
    app.task_menu.add_command(label='复制链接', command=app.copy_selected)
    # Single-task actions are looked up by label so inserting a menu item cannot disable the wrong entry.
    app.single_task_menu_labels = (
        '填写前预览',
        '活动图片与群二维码',
        '备注 / 准备清单 / 专属提醒',
        '编辑活动时间 / 截止时间',
    )
    app.task_menu_index = {
        app.task_menu.entrycget(i, 'label'): i
        for i in range(app.task_menu.index('end') + 1)
        if app.task_menu.type(i) != 'separator'
    }
    taskbutton = ttk.Menubutton(bottom, text='任务操作', menu=app.task_menu)
    taskbutton.pack(side='left', padx=8)
    app.preview_button = ttk.Button(
        bottom, text='填写前预览', style='Primary.TButton', command=lambda: show_fill_preview(app)
    )
    app.preview_button.pack(side='right')
    app.info_button = ttk.Button(bottom, text='完整信息', command=lambda: show_full_detail(app), state='disabled')
    app.full_detail = None

    def navigate_detail(event=None):
        if event is not None and getattr(event, 'num', None) == 1 and not app.table.identify_row(event.y):
            return
        if not app.table.selection():
            return
        app.list_detail.show_detail()
        if app.list_detail.wide is False:
            app.detail_tabs['通知原文'].focus_set()
        return 'break'

    def navigate_list(event=None):
        if app.list_detail.wide is not False:
            return
        app.list_detail.show_list()
        app.table.focus_set()
        return 'break'

    def navigate():
        if app.list_detail.detail_requested:
            navigate_list()
        else:
            navigate_detail()

    app.details_button = ttk.Button(bottom, text='查看详情', command=navigate, state='disabled')

    def layout_changed(wide, detail):
        if wide:
            app.details_button.pack_forget()
        else:
            app.details_button.configure(
                text='返回列表' if detail else '查看详情',
                state='normal' if detail or app.table.selection() else 'disabled',
            )
            app.details_button.pack(side='left', padx=8)

    app.list_detail = ListDetailLayout(panes, left, right, layout_changed)
    app.table.bind('<Double-1>', navigate_detail)
    app.table.bind('<Return>', navigate_detail)

    def popup(e):
        iid = app.table.identify_row(e.y)
        if iid:
            if iid not in app.table.selection():
                app.table.selection_set(iid)
            app.task_menu.tk_popup(e.x_root, e.y_root)

    app.table.bind('<Button-3>', popup)

    def selected(e):
        from fill_preview import update_preview_button

        update_preview_button(app)
        if not app.table.selection():
            app.list_detail.show_list()
        if not app.table.selection():
            render_detail(app, None)
        elif getattr(app, 'detail_selection', None) != tuple(app.table.selection()):
            set_header(app, '正在读取任务详情…', '')
            for text in app.detail_tabs.values():
                set_text(text, '')
        refresh_detail(app)
        if app.table.selection():
            app.unread_new = 0
            app.root.title(window_title())

    app.table.bind('<<TreeviewSelect>>', selected)
    app.empty_panel = ttk.Frame(left, padding=16)
    app.empty_hint = ttk.Label(
        app.empty_panel,
        text='还没有任务\n设置目标群后开始监测，或点击“导入通知”手动添加。',
        justify='center',
        wraplength=360,
    )
    app.empty_hint.pack()
    app.empty_actions = ttk.Frame(app.empty_panel)
    app.empty_import = ttk.Button(app.empty_actions, text='导入通知', command=lambda: import_notice(app))
    app.empty_setup = ttk.Button(
        app.empty_actions, text='设置目标群', style='Primary.TButton', command=lambda: open_monitor_settings(app)
    )
    app.empty_reset = ttk.Button(app.empty_actions, text='重置筛选', command=reset_filters)
    return content_area, right, navigate_list


def _build_detail_panel(app, root, right, navigate_list):
    """Detail header, metadata line, contextual action and detail tabs."""
    app.selection_hint = tk.StringVar(value='选择一个任务后查看详情或预览填写')
    app.detail_title = tk.StringVar(value='选择任务查看详情')
    detail_header = ttk.Frame(right)
    detail_header.pack(fill='x', pady=(0, 8))
    detail_action = ttk.Button(detail_header, text='标记已读', width=8)
    detail_action.pack(side='right', padx=(8, 0))
    detail_title_label = SummaryLabel(detail_header, app.detail_title, font=FONTS['section'])
    app.detail_title_label = detail_title_label
    detail_title_label.pack(side='left', fill='x', expand=True)
    app.detail_meta = tk.StringVar()
    detail_meta_label = SummaryLabel(right, app.detail_meta, style='Muted.TLabel')
    detail_meta_label.pack(fill='x', pady=(0, 10))
    app.detail_meta_label = detail_meta_label
    tabs = ttk.Notebook(right)
    tabs.pack(fill='both', expand=True)
    meta_layout_tall = [None]

    def show_detail_meta(*_):
        tall = filter_inline(right.winfo_height(), dp(root, 180), meta_layout_tall[0], dp(root, 8))
        meta_layout_tall[0] = tall
        if app.detail_meta.get() and tall:
            if not detail_meta_label.winfo_manager():
                detail_meta_label.pack(fill='x', pady=(0, 10), before=tabs)
        elif detail_meta_label.winfo_manager():
            detail_meta_label.pack_forget()

    app.detail_meta.trace_add('write', show_detail_meta)
    right.bind('<Configure>', ResizeUpdate(right, show_detail_meta).request, add='+')
    show_detail_meta()

    def read_update():
        for url in app.table.selection():
            app.store.read_notice(url)
        app.refresh_table()

    detail_actions = {
        '通知原文': ('标记已读', read_update),
        '活动时间': ('编辑时间', lambda: edit_dates(app)),
        '处理信息': ('编辑备注', lambda: edit_activity(app)),
    }

    def change_detail_action(*_):
        selected_tab = tabs.select()
        if not selected_tab:
            return
        action = detail_actions.get(tabs.tab(selected_tab, 'text'))
        if action:
            detail_action.configure(
                text=action[0], command=action[1], state='normal' if len(app.table.selection()) == 1 else 'disabled'
            )
            detail_action.pack(side='right', padx=(8, 0), before=detail_title_label)
        else:
            detail_action.pack_forget()

    tabs.bind('<<NotebookTabChanged>>', change_detail_action)
    app.table.bind('<<TreeviewSelect>>', change_detail_action, add='+')
    app.detail_tabs = {}
    for label in ['通知原文', '待补填', '活动时间', '处理信息']:
        frame = ttk.Frame(tabs)
        tabs.add(frame, text=label)
        content = ttk.Frame(frame)
        content.pack(fill='both', expand=True)
        text = tk.Text(
            content,
            wrap='word',
            state='disabled',
            font=FONTS['body'],
            padx=8,
            pady=8,
            borderwidth=0,
            width=36,
            height=10,
        )
        scroll = ttk.Scrollbar(content, orient='vertical', command=text.yview)
        text.configure(yscrollcommand=scroll.set)
        text.pack(side='left', fill='both', expand=True)
        scroll.pack(side='right', fill='y')
        app.detail_tabs[label] = text
        text.bind('<Escape>', navigate_list)


def _build_task_status(app, root, outer, status_row):
    """Task-processing status card, maintenance/file rows and footer credit."""
    app.status = StatusVar(root, value='使用前请查看“更多 → 关于与软件声明”；表单最终由你提交。')
    task_status_box = _status_card(status_row, '任务处理动态', 1)
    app.task_status_box = task_status_box
    app.process_button = ttk.Button(task_status_box, text='继续处理', command=app.resume)
    app.process_button.pack(side='right', anchor='n', padx=(12, 0))
    status_label = StatusLabel(task_status_box, app.status, wraplength=480)
    status_label.pack(anchor='w')
    bind_wrap(
        task_status_box,
        status_label,
        lambda e: max(120, e.width - dp(root, 44) - app.process_button.winfo_reqwidth()),
    )
    app.maintenance_activity = StatusVar(
        root,
    )
    app.maintenance_row = ttk.Frame(task_status_box)
    maintenance_label = StatusLabel(app.maintenance_row, app.maintenance_activity, wraplength=850)
    maintenance_label.pack(side='left', fill='x', expand=True)
    app.maintenance_retry_button = ttk.Button(app.maintenance_row, text='重试检查', command=app.retry_maintenance)
    app.maintenance_retry_button.pack(side='right')
    bind_wrap(app.maintenance_row, maintenance_label, lambda e: max(160, e.width - 130))
    app.file_activity = StatusVar(
        root,
    )
    app.file_status_row = ttk.Frame(task_status_box)
    StatusLabel(app.file_status_row, app.file_activity).pack(side='left')
    app.file_progress = ttk.Progressbar(app.file_status_row, mode='indeterminate', length=100)
    app.file_progress.pack(side='right')
    ttk.Label(outer, text='© 2026 Chen Zhuo  ·  Designed by Chen Zhuo', style='ShellMuted.TLabel').pack(
        side='bottom', before=app.task_card, anchor='e', pady=(8, 0)
    )
    return status_label


def _bind_responsive_layout(
    app, root, outer, card, summary_label, monitor_label, status_label, filters, filter_options, tools
):
    """Width-driven wrapping, list/detail switching and filter-row placement (40 ms coalesced).

    The two status cards wrap their own text (bind_wrap on each card)."""
    compact_filters = [None]
    last_width = [None]

    def resize(event):
        if event.width == last_width[0]:
            return
        last_width[0] = event.width
        app.list_detail.update_width(event.width)
        set_wrap(summary_label, max(300, event.width - dp(root, 40)))
        inline = filter_inline(event.width, dp(root, 840), compact_filters[0], dp(root, 16))
        if inline != compact_filters[0]:
            filter_options.pack_forget()
            if inline:
                filter_options.pack(in_=filters, side='right')
            else:
                filter_options.pack(in_=card, fill='x', pady=(0, 8), before=tools)
            compact_filters[0] = inline

    outer.bind('<Configure>', ResizeUpdate(outer, resize).request)


# Row colour only marks the few states that need attention or are finished;
# the state text itself always stays readable in the 状态 column.
ROW_TAGS = {
    '打开失败': ('error',),
    '需要手动重试': ('error',),
    '已处理': ('muted',),
    '已忽略': ('muted',),
}


def row_tags(values):
    return ROW_TAGS.get(values[1], ()) if len(values) > 1 else ()


def set_text(widget, text):
    if widget.get('1.0', 'end-1c') == text:
        return
    widget.configure(state='normal')
    widget.delete('1.0', 'end')
    widget.insert('1.0', text)
    widget.configure(state='disabled')


def refresh_detail(app):
    # Selection changes request a new snapshot; no database reads on the UI thread.
    app.refresh_table()


def render_detail(app, detail):
    if len(app.table.selection()) > 1:
        app.detail_selection = tuple(app.table.selection())
        set_header(
            app,
            f'已选择 {len(app.detail_selection)} 项任务',
            '可批量打开、暂缓或标记处理；编辑和填写预览需选择单个任务。',
        )
        for widget in app.detail_tabs.values():
            set_text(widget, '')
        return
    app.detail_selection = tuple(app.table.selection())
    title = detail['title'] if detail else '选择任务查看详情'
    meta = detail['meta'] if detail else ''
    held = False
    if detail and app.detail_selection:
        from open_policy import auto_open_allowed

        url = app.detail_selection[0]
        if not auto_open_allowed(url, app.get_config()):
            held = True
            with app.config_lock:
                manual = url in app.manual_open_urls
            displayed = app.table.item(url, 'values')
            if len(displayed) > 1 and displayed[1] in ('等待手动打开', '已手动排队'):
                _, separator, rest = meta.partition('  ·  来源：')
                if separator:
                    meta = displayed[1] + separator + rest
            meta += '\n自动跳转已关闭 · ' + ('本次已手动排队' if manual else '需要时选择“打开网页”')
    set_header(app, title, meta + '\n' + app.selection_hint.get() if detail else '', ready=bool(detail))
    for name, widget in app.detail_tabs.items():
        text = detail['tabs'].get(name, '') if detail else ''
        if held and name == '处理信息':
            text += '\n\n此类链接自动跳转已关闭；上方重试时间不触发自动打开，未开始的任务须手动排队。'
        set_text(widget, text)


def show_logs(app):
    win = tk.Toplevel(app.root)
    win.title('运行记录')
    fit_window(win, 760, 420)
    text = tk.Text(win, wrap='word', font=FONTS['body'])
    text.pack(fill='both', expand=True, padx=8, pady=8)
    text.insert('1.0', '\n'.join(app.log_lines))
    text.configure(state='disabled')
    themed(app, win)


def edit_dates(app):
    selection = app.table.selection()
    if len(selection) != 1:
        messagebox.showinfo('选择一个任务', '请先选中一个任务，再编辑其活动时间。', parent=app.root)
        return
    if getattr(app, 'dates_window', None) and app.dates_window.winfo_exists():
        app.dates_window.lift()
        return
    url = selection[0]
    row = app.store.task(url)
    from dates_dialog_ui import show_dates_dialog

    show_dates_dialog(app, url, row)


def notify_new(app, count):
    config = app.get_config()
    if not config.get('notify_new', True):
        return
    app.unread_new += count
    app.root.title(window_title(f'新增 {app.unread_new} 项'))
    if config.get('notify_sound', False):
        try:
            app.root.bell()
        except tk.TclError:
            pass
    if not config.get('notify_popup', True):
        return
    if not app.reminder or not app.reminder.winfo_exists():
        win = tk.Toplevel(app.root)
        app.reminder = win
        win.title('新增活动任务')
        win.resizable(False, False)
        win.attributes('-topmost', True)
        frame = ttk.Frame(win, padding=dp(win, 16))
        frame.pack(fill='both', expand=True)
        app.reminder_text = tk.StringVar()
        app.reminder_body = tk.StringVar(value='链接和可读取的通知原文已保存。')
        ttk.Label(frame, textvariable=app.reminder_text, style='Strong.TLabel').pack(anchor='w')
        ttk.Label(frame, textvariable=app.reminder_body, style='Muted.TLabel', wraplength=dp(win, 320)).pack(
            anchor='w', pady=(dp(win, 4), 0)
        )

        def view():
            app.root.deiconify()
            app.root.lift()
            app.unread_new = 0
            app.root.title(window_title())
            win.destroy()

        actions = ttk.Frame(frame)
        actions.pack(fill='x', pady=(dp(win, 14), 0))
        ttk.Button(actions, text='查看任务清单', style='Primary.TButton', command=view).pack(
            side='left', fill='x', expand=True
        )
        ttk.Button(actions, text='稍后', command=win.destroy).pack(
            side='left', fill='x', expand=True, padx=(dp(win, 8), 0)
        )
        width, height = dp(win, 372), dp(win, 150)
        bounds = work_area(app.root)
        win.geometry(f'{width}x{height}+{max(bounds[0], bounds[2] - width - dp(win, 16))}+{max(bounds[1], bounds[3] - height - dp(win, 16))}')
    app.reminder_text.set(f'新增 {app.unread_new} 项活动任务')
    if app.reminder_timer:
        try:
            app.root.after_cancel(app.reminder_timer)
        except tk.TclError:
            pass

    def dismiss():
        if app.reminder and app.reminder.winfo_exists():
            app.reminder.destroy()

    app.reminder_timer = app.root.after(10000, dismiss)
    themed(app, app.reminder)


def update_task_selection(app):
    selected = app.table.selection()
    count = len(selected)
    if hasattr(app, 'details_button'):
        app.details_button.configure(state='normal' if count or app.list_detail.detail_requested else 'disabled')
    text = '打开网页'
    if count == 1:
        values = app.table.item(selected[0], 'values')
        if len(values) > 1 and values[1] in ('打开失败', '等待重试', '需要手动重试'):
            text = '重试打开'
    elif count > 1:
        text = f'打开选中链接（{count}）'
    key = (count, text, app.processing.is_set())
    if getattr(app, 'selection_controls_key', None) == key:
        return
    app.selection_controls_key = key
    app.open_button.configure(text=text, state='normal' if count else 'disabled')
    app.selection_hint.set(
        f'已选 {count} 项 · 编辑和预览需单选'
        if count > 1
        else '处理已暂停，打开链接需先点击“继续处理”'
        if count and not app.processing.is_set()
        else '选择一个任务后查看详情或预览填写'
        if not count
        else '先核对通知，再预览填写'
    )
    for label in app.single_task_menu_labels:
        app.task_menu.entryconfigure(app.task_menu_index[label], state='normal' if count == 1 else 'disabled')


def show_empty(app, message, mode=None):
    app.empty_hint.configure(text=message)
    for button in (app.empty_import, app.empty_setup, app.empty_reset):
        button.pack_forget()
    app.empty_actions.pack_forget()
    if mode:
        app.empty_actions.pack(pady=(12, 0))
        buttons = (app.empty_import, app.empty_setup) if mode == 'new' else (app.empty_reset,)
        for button in buttons:
            button.pack(side='left', padx=4)
    app.empty_panel.place(relx=0.5, rely=0.4, anchor='center')


def open_monitor_settings(app):
    app.open_settings()
    app.settings_notebook.select(1)
    app.settings_notebook.ensure_selected()
