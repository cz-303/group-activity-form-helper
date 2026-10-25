import time

_IMPORT_STARTED = time.perf_counter()
from background_jobs import BackgroundJobs
from maintenance import run_maintenance
from task_query import TaskViewReader, LatestQuery, ViewRequest
from file_operations import prepare_restore, install_pending, export_tasks
from ui_layout import settings_tab, fit_window, LazyNotebook
from diagnostics import describe_error
import ctypes
from ctypes import wintypes
import csv
import json
from pathlib import Path
import queue
import subprocess
import sys
import threading
import tkinter as tk
from tkinter import ttk, messagebox, filedialog
from core import DEFAULTS, SAMPLE, canonical_url, fill_known_fields, capture_notifications, validate_custom_fields
from task_links import canonical_task_url, kind_label
from open_policy import OPEN_KEYS, auto_open_allowed, queue_filter, open_failure_notice
from chat_reader import read_group_windows
from task_store import TaskStore
from field_report import inspect_fields
from ui import build_main, refresh_detail, render_detail, notify_new, show_empty, row_tags
from reminders import MonitorHealth
from appearance import themed
from management_ui import show_alerts, alert_popup, extra_settings
from backup_support import make_backup, inspect_backup, stage_restore, apply_pending
from fill_preview import build_plan, signature as plan_signature, apply_approved

ROOT = Path(sys.executable).parent if getattr(sys, 'frozen', False) else Path(__file__).resolve().parent
DATA = ROOT / 'data'
from copy import deepcopy
from settings import SETTINGS, validate_settings, load_settings, save_settings
from storage_io import atomic_text, spreadsheet_cell
from diagnostics import create_logger, record_error, close_logger, ErrorRateLimiter

from adapters.chat_client import client_process, process_path, read_group


class App:
    def __init__(self, root, data_dir=DATA):
        self.root, self.data = root, Path(data_dir)
        self.data.mkdir(parents=True, exist_ok=True)
        self.logger = create_logger(self.data)
        self.error_limiter = ErrorRateLimiter()
        restored = apply_pending(self.data, validate_settings)
        self.store = TaskStore(self.data / 'tasks.sqlite3')
        self.store.recover_inflight()
        self.query = LatestQuery(TaskViewReader(self.store.path), self.render_table, self.query_failed)
        self.query_retry_at = 0
        self.query_failed_request = None
        self.query_started = None
        self.events = queue.Queue()
        self.jobs = BackgroundJobs()
        self.maintenance = BackgroundJobs()
        self.pending_alerts = {}
        self.maintenance_retry_at = 0
        self.maintenance_started = None
        self.maintenance_failed = False
        self.quit_event, self.processing = threading.Event(), threading.Event()
        self.config_lock = threading.Lock()
        self.manual_open_urls = set()
        self.preview_lock = threading.Lock()
        self.previews = {}
        self.sheet_requests = queue.Queue(maxsize=1)
        self.monitor_thread = self.browser_thread = self.monitor_stop = None
        self.browser_backoff = 0
        self.closing = False
        self.purge_requested = False
        self.settings_window = None
        self.tray = None
        self.tray_hide_pending = False
        self.close_window = None
        self.health = MonitorHealth()
        self.monitor_requested = False
        self.monitor_success_mono = None
        self.backup_thread = None
        self.next_backup_check = time.monotonic() + 10
        self.next_alert_check = 0
        self.table_snapshot = None
        config, warning = load_settings(self.data / 'config.json')
        if warning:
            self.events.put(warning)
        self.config = config
        if config['auto_resume']:
            self.processing.set()
        build_main(self, root)
        from brand_icon import install_window_icon

        install_window_icon(root)
        themed(self)
        if restored:
            self.emit(restored)
        root.protocol('WM_DELETE_WINDOW', self.close)
        self.tick_timer = root.after(300, self.tick)
        self.query_timer = root.after(50, self.poll_query)
        root.bind('<Destroy>', self.on_destroy, add='+')
        root.report_callback_exception = self.callback_error

    def can_start_file_job(self):
        if self.closing:
            return False
        if self.jobs.busy or (self.backup_thread and self.backup_thread.is_alive()):
            self.status.set('另一个文件操作正在进行，请完成后重试。', state='warning')
            return False
        return True

    def run_file_job(self, label, work, success, cleanup=lambda value: None, feedback=None):
        if not self.can_start_file_job():
            return False

        def failed(error):
            record_error(self.logger, 'file-operation', error)
            self.status.set(label + '失败：' + describe_error(error), state='error')
            if feedback is not None:
                feedback.set(label + '未完成，请重试。', state='error')
            messagebox.showerror(label + '失败', describe_error(error), parent=self.root)

        def complete(value):
            success(value)
            if feedback is not None and feedback.state == 'running':
                feedback.set(label + '已完成。', state='success')

        prior = self.maintenance.thread
        waiting = bool(prior and prior.is_alive())

        def serialized_work():
            if prior:
                prior.join()
            if waiting:
                self.events.put(('file_started', label))
            return work()

        started = self.jobs.start(serialized_work, complete, failed, cleanup)
        if started:
            if feedback is not None:
                feedback.set(label + '，请稍候…', state='running')
            self.file_activity.set(label + ('，等待后台检查结束…' if waiting else '，请稍候…'), state='running')
            self.file_status_row.pack(fill='x', pady=(5, 0))
            self.file_progress.start(20)
        return started

    def confirm_restore(self, prepared):
        stamp = time.strftime('%Y-%m-%d %H:%M', time.localtime(prepared.created))
        if not messagebox.askyesno(
            '确认恢复 / 迁移',
            f'快照时间：{stamp}\n任务数量：{prepared.count}\n\n下次启动会替换当前任务和设置，保留恢复前副本。不会复制浏览器登录状态。\n\n确认安排恢复？',
            parent=self.root,
        ):
            self.status.set('已取消恢复，当前数据未改变。', state='cancelled')
            return

        # Approval applies to the private snapshot that was inspected, not a mutable source file.
        def complete(_):
            self.stop_monitor()
            self.processing.clear()
            self.status.set('已安排下次启动恢复。请处理未提交网页后，彻底退出并重新启动。', state='warning')

        prepared.retained = self.run_file_job('准备恢复', lambda: install_pending(prepared, self.data), complete)

    def emit(self, text, state='info'):
        self.events.put(('status', text, state))

    def emit_monitor(self, text, success=False):
        self.events.put(('monitor', text, time.time() if success else None))

    def get_config(self):
        with self.config_lock:
            return deepcopy(self.config)

    def callback_error(self, kind, error, trace):
        record_error(self.logger, 'ui-callback', error.with_traceback(trace))
        self.status.set('操作未完成，请重试；若反复出现，请查看数据目录中的诊断记录。', state='error')

    def on_destroy(self, event):
        if event.widget is not self.root:
            return
        self.closing = True
        self.query.close()
        self.maintenance.close()
        if self.maintenance.thread:
            self.maintenance.thread.join(1)
        if self.query.jobs.thread:
            self.query.jobs.thread.join(1)
        try:
            self.jobs.close()
        except Exception as error:
            record_error(self.logger, 'close-file-operation', error)
        self.quit_event.set()
        self.processing.clear()
        if self.monitor_stop:
            self.monitor_stop.set()
        if self.tray:
            self.tray.stop()
        # Cancel callbacks belonging to this interpreter before it is destroyed.
        for timer in self.root.tk.call('after', 'info'):
            try:
                self.root.tk.call('after', 'cancel', timer)
            except tk.TclError:
                pass
        close_logger(self.logger)

    def tick(self):
        if self.closing:
            return
        try:
            self.jobs.poll()
            self.maintenance.poll()
            if not self.jobs.busy:
                self.file_progress.stop()
                self.file_activity.set('')
                self.file_status_row.pack_forget()
            self._tick_once()
        except Exception as error:
            record_error(self.logger, 'refresh', error)
            self.status.set('本轮刷新未完成，稍后自动重试；任务仍保留在清单中。', state='error')
        finally:
            if not self.closing:
                self.tick_timer = self.root.after(700, self.tick)

    def _tick_once(self):
        try:
            for _ in range(100):
                text = self.events.get_nowait()
                if isinstance(text, tuple) and text[0].startswith('tray_'):
                    self.handle_tray(text)
                    continue
                if isinstance(text, tuple) and text[0] == 'file_started':
                    self.file_activity.set(text[1] + '，请稍候…', state='running')
                    continue
                if isinstance(text, tuple) and text[0] == 'backup_failed':
                    self.deliver_alert('backup-failure:' + time.strftime('%Y-%m-%d'), '数据备份失败', text[1])
                    self.status.set('备份失败：' + text[1], state='error')
                    continue
                if isinstance(text, tuple) and text[0] == 'new':
                    notify_new(self, text[1])
                    continue
                if isinstance(text, tuple) and text[0] == 'monitor':
                    if text[2] is not None:
                        self.monitor_last_success = text[2]
                    if self.monitor_requested:
                        recovered = self.health.observe(text[2] is not None)
                        if text[2] is not None:
                            self.monitor_success_mono = time.monotonic()
                        if recovered and self.get_config()['monitor_alerts']:
                            self.deliver_alert(
                                'recovered:' + str(time.time_ns()), '群聊监测已恢复', '已重新成功读取目标群。'
                            )
                    if text[1] != self.monitor_message:
                        self.log_lines.append(time.strftime('%H:%M:%S ') + '[监测] ' + text[1])
                        self.log_lines = self.log_lines[-400:]
                    self.monitor_message = text[1]
                    continue
                if isinstance(text, tuple) and text[0] == 'status':
                    _, text, state = text
                else:
                    state = 'info'
                self.status.set(text, state)
                self.log_lines.append(time.strftime('%H:%M:%S ') + text)
                self.log_lines = self.log_lines[-400:]
        except queue.Empty:
            pass
        self.check_reminders()
        self.refresh_monitor_status()
        self.update_maintenance_status()
        self.refresh_table()
        self.schedule_maintenance()

    def refresh_monitor_status(self):
        monitor = bool(
            self.monitor_thread
            and self.monitor_thread.is_alive()
            and self.monitor_stop
            and not self.monitor_stop.is_set()
        )
        stopping = bool(
            self.monitor_thread and self.monitor_thread.is_alive() and self.monitor_stop and self.monitor_stop.is_set()
        )
        monitor_state = '运行中' if monitor else ('正在停止' if stopping else '已停止')
        control_state = (monitor, stopping)
        if getattr(self, 'monitor_control_state', None) != control_state:
            self.monitor_button.configure(
                text='正在停止…' if stopping else '停止监测' if monitor else '开始监测',
                state='disabled' if stopping else 'normal',
                command=self.stop_monitor if monitor else self.start_monitor,
            )
            self.monitor_control_state = control_state
        running = self.processing.is_set()
        if getattr(self, 'process_control_state', None) != running:
            self.process_button.configure(
                text='暂停处理' if running else '继续处理', command=self.pause if running else self.resume
            )
            self.task_status_box.configure(
                text='任务处理动态 · 运行中' if running else '任务处理动态 · 已暂停，仍可捕获通知'
            )
            self.process_control_state = running
        last_ok = (
            time.strftime('%m-%d %H:%M:%S', time.localtime(self.monitor_last_success))
            if self.monitor_last_success
            else '尚无成功读取'
        )
        message = (
            self.monitor_message
            if monitor
            else (
                '等待当前读取结束' if stopping else '最近监测记录：' + self.monitor_message + '；可点击右侧“开始监测”'
            )
        )
        self.monitor_summary.set(
            f"{self.get_config()['group'] or '未设置目标群'}  ·  监测{monitor_state}  ·  最近成功读取：{last_ok}\n{message}",
            state='error'
            if monitor and self.health.warned
            else 'warning'
            if monitor and self.health.failed_since is not None
            else 'running'
            if monitor or stopping
            else 'idle',
        )

    def view_request(self):
        return ViewRequest(
            self.search_var.get(),
            self.filter_var.get(),
            self.source_var.get(),
            self.time_filter_var.get(),
            self.sort_var.get(),
            tuple(self.table.selection()),
            int(time.time() // 60),
        )

    def refresh_table(self):
        if self.closing:
            return
        self.refresh_monitor_status()
        from fill_preview import update_preview_button

        update_preview_button(self)
        if self.search_timer:
            return
        request = self.view_request()
        if request == self.query_failed_request and time.monotonic() < self.query_retry_at:
            return
        if not self.query.busy:
            self.query_started = time.monotonic()
        self.query.request(request)

    def poll_query(self):
        if self.closing:
            return
        try:
            self.query.poll()
            if self.query.busy and self.query_started is not None and time.monotonic() - self.query_started > 0.15:
                self.query_status.set('正在查询…可以继续输入或切换筛选；当前保留上次结果。', state='running')
                self.query_retry_button.configure(state='disabled')
                self.query_row.pack(fill='x', pady=(0, 4), before=self.query_row_before)
                if self.table_snapshot is None and not self.table.get_children():
                    show_empty(self, '正在读取任务…可以继续输入或切换筛选。')
            elif not self.query.busy:
                self.query_started = None
        except Exception as error:
            self.query_failed(self.view_request(), error)
        finally:
            if not self.closing:
                self.query_timer = self.root.after(50, self.poll_query)

    def retry_query(self):
        self.query_retry_at = 0
        self.refresh_table()

    def query_failed(self, request, error):
        if self.closing or request != self.view_request():
            return
        self.query_failed_request = request
        self.query_retry_at = time.monotonic() + 5
        record_error(self.logger, 'task-query', error)
        if isinstance(error, TimeoutError):
            message = '查询耗时较长，请减少关键词后重试。'
        elif isinstance(error, PermissionError):
            message = '无法读取任务数据，请检查文件访问权限或关闭占用程序后重试。'
        else:
            message = describe_error(error)
        self.query_status.set('未能刷新，保留上次结果。' + message, state='error')
        self.query_retry_button.configure(state='normal')
        self.query_row.pack(fill='x', pady=(0, 4), before=self.query_row_before)
        if not self.table.get_children():
            show_empty(self, '暂时无法读取任务，请重试。原有数据不会因此删除。')

    def render_table(self, request, result):
        if self.closing or request != self.view_request():
            return
        had_error = self.query_failed_request is not None
        self.query_failed_request = None
        self.query_retry_at = 0
        self.query_status.set('')
        self.query_retry_button.configure(state='disabled')
        self.query_row.pack_forget()
        # A cached result may be reused while the independent processing state changes.
        rows = result['rows']
        self.summary.set(
            f"任务处理{'运行中' if self.processing.is_set() else '已暂停'}  ·  共 {result['total']} 项 / 待处理 {result['pending']} 项  ·  匹配 {result['matched']} 项，显示 {len(rows)} 项（最多500项）"
        )
        if not had_error and self.table_snapshot == result['key']:
            return
        self.source_chooser.configure(values=result['sources'])
        selected = set(self.table.selection())
        ordered = self.table.get_children()
        current = set(ordered)
        order_changed = tuple(url for url, _ in rows) != ordered
        config = self.get_config()
        with self.config_lock:
            manual = set(self.manual_open_urls)
        for index, (url, values) in enumerate(rows):
            if values[1] in ('待处理', '等待重试', '需要手动重试') and not auto_open_allowed(url, config):
                values = (values[0], '已手动排队' if url in manual else '等待手动打开', *values[2:])
            tags = row_tags(values)
            if url in current:
                if self.table.item(url, 'values') != values:
                    self.table.item(url, values=values, tags=tags)
            else:
                self.table.insert('', 'end', iid=url, values=values, tags=tags)
            if order_changed:
                self.table.move(url, '', index)
        remaining = {url for url, _ in rows}
        for iid in current - remaining:
            self.table.delete(iid)
        wanted = selected & remaining
        if set(self.table.selection()) != wanted:
            self.table.selection_set(list(wanted))
        if rows:
            self.empty_panel.place_forget()
        else:
            show_empty(
                self,
                '当前筛选下没有任务。' if result['total'] else '还没有任务\n设置目标群后开始监测，或导入通知手动添加。',
                'filtered' if result['total'] else 'new',
            )
        from fill_preview import update_preview_button

        update_preview_button(self)
        render_detail(self, result['detail'] if wanted else None)
        unread = result['unread']
        self.alert_button.configure(text=f'提醒中心 ({unread})' if unread else '提醒中心')
        self.table_snapshot = result['key']

    def deliver_alert(self, key, title, body):
        self.pending_alerts[key] = (key, title, body)

    def check_reminders(self):
        config = self.get_config()
        now = time.monotonic()
        if self.monitor_requested and config['monitor_alerts']:
            age = now - self.monitor_success_mono if self.monitor_success_mono is not None else None
            if self.health.check(
                age,
                config['monitor_alert_seconds'],
                now,
                max(config['monitor_alert_seconds'], config['poll_seconds'] * 2 + 5),
            ):
                self.deliver_alert(
                    'monitor:' + self.health.session + ':' + str(time.time_ns()),
                    '群聊监测异常',
                    f"持续无法正常读取，或监测响应超过允许等待时间。请检查聊天客户端是否展开、停留在目标群，或重新开始监测。\n最近状态：{self.monitor_message}",
                )

    def update_maintenance_status(self):
        if self.maintenance_failed:
            return
        if (
            self.maintenance.busy
            and self.maintenance_started is not None
            and time.monotonic() - self.maintenance_started > 2
        ):
            self.maintenance_activity.set('后台检查仍在进行；你可以继续搜索和查看任务。', state='running')
            self.maintenance_retry_button.configure(state='disabled')
            self.maintenance_row.pack(fill='x', pady=(5, 0))
        else:
            self.maintenance_row.pack_forget()

    def retry_maintenance(self):
        self.maintenance_retry_at = 0
        self.schedule_maintenance()

    def schedule_maintenance(self):
        now = time.monotonic()
        if (
            self.closing
            or self.maintenance.busy
            or self.jobs.busy
            or (self.backup_thread and self.backup_thread.is_alive())
            or now < self.maintenance_retry_at
        ):
            return
        config = self.get_config()
        check_alerts = now >= self.next_alert_check
        check_backup = now >= self.next_backup_check
        batch = list(self.pending_alerts.values())[:100]
        with self.config_lock:
            manual = tuple(self.manual_open_urls)

        def complete(result):
            recovered = self.maintenance_failed
            for item in batch:
                if self.pending_alerts.get(item[0]) == item:
                    self.pending_alerts.pop(item[0], None)
            self.maintenance_failed = False
            self.maintenance_row.pack_forget()
            if recovered:
                self.status.set(
                    '后台检查已恢复，任务处理已继续。'
                    if self.processing.is_set()
                    else '后台检查已恢复；任务处理仍保持暂停，请核对后点击继续处理。',
                    state='success' if self.processing.is_set() else 'warning',
                )
            if result.alerts:
                alert_popup(self, result.alerts)
            if result.backup_error:
                record_error(self.logger, 'backup-check', result.backup_error)
                self.deliver_alert(
                    'backup-check:' + time.strftime('%Y-%m-%d'), '自动备份检查失败', describe_error(result.backup_error)
                )
            if result.backup_due and self.get_config()['backup_enabled']:
                if self.jobs.busy or (self.backup_thread and self.backup_thread.is_alive()):
                    self.next_backup_check = 0
                else:
                    self.start_backup(True)
            if (
                result.due
                and all(config[k] == self.get_config()[k] for k in OPEN_KEYS)
                and not self.closing
                and self.processing.is_set()
                and not self.jobs.busy
                and not (self.backup_thread and self.backup_thread.is_alive())
                and time.time() >= self.browser_backoff
                and not (self.browser_thread and self.browser_thread.is_alive())
            ):
                self.browser_thread = threading.Thread(target=self.browser, daemon=True)
                self.browser_thread.start()

        def failed(error):
            self.processing.clear()
            self.maintenance_failed = True
            self.maintenance_retry_at = time.monotonic() + 5
            self.next_alert_check = 0
            if check_backup:
                self.next_backup_check = 0
            record_error(self.logger, 'maintenance', error)
            self.maintenance_activity.set(
                '后台检查失败，已暂停任务处理；将自动重试。' + describe_error(error), state='error'
            )
            self.maintenance_retry_button.configure(state='normal')
            self.maintenance_row.pack(fill='x', pady=(5, 0))

        started = self.maintenance.start(
            lambda: run_maintenance(
                self.store,
                self.data,
                config,
                batch,
                check_reminders=check_alerts,
                check_backup=check_backup,
                manual_urls=manual,
            ),
            complete,
            failed,
        )
        if started:
            self.maintenance_started = now
            self.maintenance_retry_button.configure(state='disabled')
            if check_alerts:
                self.next_alert_check = now + 5
            if check_backup:
                self.next_backup_check = now + 60

    def start_backup(self, automatic=False):
        if self.jobs.busy:
            if not automatic:
                self.status.set('另一个文件操作正在进行，请稍候再备份。', state='warning')
            return
        if self.backup_thread and self.backup_thread.is_alive():
            if not automatic:
                self.emit('备份正在进行，请稍候。', state='running')
            return
        config = self.get_config()
        prior = self.maintenance.thread

        def run():
            try:
                if prior:
                    prior.join()
                path = make_backup(
                    self.store, self.data, config, 'auto' if automatic else 'manual', config['backup_keep']
                )
                if not automatic:
                    self.emit('备份已保存：' + str(path), state='success')
            except Exception as e:
                record_error(self.logger, 'backup', e)
                self.events.put(('backup_failed', describe_error(e)))

        self.backup_thread = threading.Thread(target=run, name='activity-backup', daemon=False)
        self.backup_thread.start()

    def choose_restore(self):
        if not self.can_start_file_job():
            return
        path = filedialog.askopenfilename(
            title='选择助手备份', initialdir=self.data / 'backups', filetypes=[('助手备份', '*.zip')]
        )
        if not path:
            return
        self.run_file_job(
            '检查备份',
            lambda: prepare_restore(path),
            self.confirm_restore,
            lambda prepared: prepared.discard_unless_retained(),
        )

    def ignore_selected(self):
        n = self.store.disposition(self.table.selection(), 'ignored')
        with self.config_lock:
            self.manual_open_urls.difference_update(self.table.selection())
        self.emit(f'已忽略 {n} 项。链接和原文仍保留；已打开网页不会关闭。打开中或已处理的任务不变。', state='success')
        self.refresh_table()

    def defer_selected(self, seconds):
        n = self.store.disposition(self.table.selection(), 'snoozed', time.time() + seconds)
        with self.config_lock:
            self.manual_open_urls.difference_update(self.table.selection())
        self.emit(f'已暂缓 {n} 项，到期恢复原状态；已打开网页不会关闭。', state='success')
        self.refresh_table()

    def restore_selected(self):
        n = self.store.disposition(self.table.selection(), 'active')
        self.emit(f'已恢复 {n} 项原状态。需要重试的任务可用“重新排队”。', state='success')
        self.refresh_table()

    def manual_add(self):
        url = canonical_task_url(self.url_var.get())
        if not url:
            messagebox.showerror('链接不支持', '请输入腾讯收集表、腾讯在线表格或金山文档的 HTTPS 分享链接。')
            return
        try:
            n = self.store.add_many([url])
            self.emit(
                '链接已保存到待处理清单。' if n else '这个链接已在清单中；如需再次打开，请选中后点击“重新排队”。',
                state='success' if n else 'info',
            )
        except Exception as e:
            messagebox.showerror('未能保存链接', describe_error(e))

    def resume(self):
        if self.jobs.busy:
            self.status.set('文件操作正在进行，请完成后继续处理。', state='warning')
            return
        self.processing.set()
        self.browser_backoff = 0
        self.emit('已继续处理；仅打开允许自动跳转或本次手动排队的任务，其他链接继续保留。', state='running')

    def pause(self):
        self.processing.clear()
        self.emit('已暂停领取新任务；正在打开的网页可能完成打开，已打开页面保留。', state='warning')

    def requeue_selected(self):
        urls = self.table.selection()
        if not urls:
            return
        try:
            n = self.store.requeue(urls)
            pending = [url for url in urls if self.store.state(url) == 'pending']
            with self.config_lock:
                self.manual_open_urls.update(pending)
            self.table_snapshot = None
            self.refresh_table()
            self.browser_backoff = 0
            self.emit(
                f'{n}项已手动排队，本次可打开，不更改自动跳转设置。若处理已暂停，请点击继续处理。', state='success'
            )
        except Exception as e:
            messagebox.showerror('操作失败', describe_error(e))

    def done_selected(self):
        if not self.table.selection():
            return
        try:
            self.store.done(self.table.selection())
            with self.config_lock:
                self.manual_open_urls.difference_update(self.table.selection())
            self.emit('已标记所选任务为已处理；打开中的任务不变。这不代表网页已提交。', state='info')
        except Exception as e:
            messagebox.showerror('操作失败', describe_error(e))

    def copy_selected(self):
        if self.table.selection():
            self.root.clipboard_clear()
            self.root.clipboard_append('\n'.join(self.table.selection()))
            self.emit('所选链接已复制。', state='success')

    def export(self):
        if not self.can_start_file_job():
            return
        path = filedialog.asksaveasfilename(
            title='导出全部任务',
            defaultextension='.csv',
            filetypes=[('CSV 文件', '*.csv')],
            initialfile='活动链接清单.csv',
        )
        if not path:
            return
        self.run_file_job(
            '导出任务',
            lambda: export_tasks(self.store.path, path),
            lambda count: self.status.set(f'已导出 {count} 项任务，包括界面未显示的历史记录。', state='success'),
        )

    def open_settings(self):
        if self.settings_window and self.settings_window.winfo_exists():
            self.settings_window.lift()
            return
        win = tk.Toplevel(self.root)
        self.settings_window = win
        win.title('设置')
        fit_window(win, 820, 680, (640, 460))
        win.transient(self.root)
        notebook = LazyNotebook(win, lambda frame: themed(self, frame))
        notebook.pack(fill='both', expand=True, padx=15, pady=12)
        self.settings_notebook = notebook
        config = self.get_config()
        variables = {}
        sections = [
            (
                '个人资料',
                [
                    ('姓名', '姓名'),
                    ('学号', '学号'),
                    ('班级', '班级'),
                    ('学院', '学院'),
                    ('联系电话', '联系电话（可留空）'),
                ],
            ),
            (
                '群聊监测',
                [('group', '目标群完整名称'), ('client_path', '客户端程序路径'), ('poll_seconds', '检查间隔（秒，2—60）')],
            ),
            (
                '任务处理',
                [
                    ('max_tabs', '同时打开表单数（1—12）'),
                    ('max_attempts', '最大尝试次数（1—10）'),
                    ('retry_seconds', '重试基础间隔（秒，5—600）'),
                ],
            ),
            ('提醒', []),
        ]

        def build_section(title, fields, parent):
            frame = settings_tab(notebook, title, parent=parent)
            frame.columnconfigure(1, weight=1)
            if title == '任务处理':
                from task_settings_ui import build_task_settings

                build_task_settings(frame, config, variables, fields)
                return
            for row, (key, label) in enumerate(fields):
                ttk.Label(frame, text=label).grid(row=row, column=0, sticky='w', pady=8, padx=(0, 12))
                v = tk.StringVar(value=str(config[key]))
                variables[key] = v
                ttk.Entry(frame, textvariable=v).grid(row=row, column=1, sticky='ew', pady=8)
            if title == '提醒':
                for row, (key, label) in enumerate(
                    [
                        ('notify_new', '启用新增任务提醒'),
                        ('notify_popup', '显示桌面提醒浮窗'),
                        ('notify_sound', '同时播放提示音'),
                    ]
                ):
                    v = tk.BooleanVar(value=config[key])
                    variables[key] = v
                    ttk.Checkbutton(frame, text=label, variable=v).grid(
                        row=row, column=0, columnspan=2, sticky='w', pady=12
                    )
                for row, (key, label) in enumerate(
                    [
                        ('notify_updates', '原通知内容更新时提醒'),
                        ('monitor_alerts', '连续无法读取群聊时提醒，恢复后也提示'),
                        ('deadline_reminders', '启用报名截止提醒'),
                    ],
                    3,
                ):
                    v = tk.BooleanVar(value=config[key])
                    variables[key] = v
                    ttk.Checkbutton(frame, text=label, variable=v).grid(
                        row=row, column=0, columnspan=2, sticky='w', pady=9
                    )
                for row, key, label, value in [
                    (
                        6,
                        'monitor_alert_seconds',
                        '连续异常多少秒后提醒（10—600）',
                        str(config['monitor_alert_seconds']),
                    ),
                    (
                        7,
                        'deadline_minutes',
                        '提前多少分钟提醒（逗号分隔）',
                        ','.join(map(str, config['deadline_minutes'])),
                    ),
                ]:
                    ttk.Label(frame, text=label).grid(row=row, column=0, sticky='w', pady=8)
                    v = tk.StringVar(value=value)
                    variables[key] = v
                    ttk.Entry(frame, textvariable=v).grid(row=row, column=1, sticky='ew', pady=8)
                ttk.Label(
                    frame,
                    text='例如 60,30：还剩1小时、30分钟时各提醒一次；1440为提前一天。\n读取电脑当前时间，每5秒检查；须先保存活动的截止时间。\n已处理、忽略、暂缓的任务不提醒。离线期间跨过多个提前量只补一条；过期不补发。\n浮窗、提示音开关适用于各类提醒；通知更新和截止提醒记录可在提醒中心查看。',
                    wraplength=720,
                ).grid(row=8, column=0, columnspan=2, sticky='w', pady=8)
            if title == '群聊监测':
                v = tk.BooleanVar(value=config['capture_qr'])
                variables['capture_qr'] = v
                ttk.Checkbutton(frame, text='尝试捕获可见群二维码（实验性，需聊天窗口在前台）', variable=v).grid(
                    row=3, column=0, columnspan=2, sticky='w', pady=8
                )
                ttk.Label(
                    frame,
                    text='只读取目标群消息中可见且无遮挡的图片；先存入待关联图片，由你确认活动。\n不能读取的图片可在“更多 → 待关联图片”导入或粘贴。',
                    wraplength=560,
                ).grid(row=4, column=0, columnspan=2, sticky='w', pady=8)

                def choose():
                    path = filedialog.askopenfilename(
                        title='选择聊天客户端程序', filetypes=[('程序', '*.exe')], parent=win
                    )
                    if path:
                        variables['client_path'].set(path)

                ttk.Button(frame, text='选择客户端程序', command=choose).grid(row=5, column=0, sticky='w')
                ttk.Button(frame, text='按已保存配置启动客户端辅助模式', command=self.launch_client).grid(
                    row=5, column=1, sticky='w'
                )
                ttk.Label(
                    frame,
                    text='聊天客户端保持展开、停留在目标群。辅助模式按钮不会强制退出客户端。\n修改监测设置后保存会停止监测，请再点击主界面的开始监测。',
                    wraplength=560,
                ).grid(row=6, column=0, columnspan=2, sticky='w', pady=16)

        for title, fields in sections:
            notebook.add_lazy(title, lambda parent, title=title, fields=fields: build_section(title, fields, parent))
        from profile_editor import build_custom_editor

        custom_reader = [lambda: dict(config.get('custom_fields', {}))]

        def build_custom(parent):
            custom_reader[0] = build_custom_editor(notebook, config.get('custom_fields', {}), parent=parent)

        notebook.add_lazy('自定义资料', build_custom)
        notebook.add_lazy('外观与备份', lambda parent: extra_settings(self, notebook, config, variables, parent=parent))
        notebook.ensure_selected()
        bar = ttk.Frame(win)
        bar.pack(side='bottom', before=notebook, fill='x', padx=15, pady=(0, 15))

        def save():
            try:
                if notebook.failed:
                    raise ValueError('设置页加载失败，请关闭设置后重试，当前配置未保存。')
                values = dict(config)
                values.update({k: v.get() for k, v in variables.items()})
                values['custom_fields'] = custom_reader[0]()
                new = validate_settings(values)
                save_settings(self.data / 'config.json', new)
                previous = self.get_config()
                with self.config_lock:
                    self.config = new
                self.table_snapshot = None
                self.refresh_table()
                if previous['theme'] != new['theme']:
                    themed(self)
                self.next_alert_check = 0
                self.next_backup_check = time.monotonic() + 1
                if any(new[k] != previous[k] for k in ('group', 'poll_seconds', 'capture_qr')):
                    self.stop_monitor()
                self.emit('设置已保存。个人资料和任务参数已更新；启动恢复开关在下次启动时生效。', state='success')
                win.destroy()
            except Exception as e:
                messagebox.showerror('无法保存设置', describe_error(e), parent=win)

        ttk.Button(bar, text='保存设置', style='Primary.TButton', command=save).pack(side='right')
        ttk.Button(bar, text='取消', command=win.destroy).pack(side='right', padx=8)
        themed(self, win)

    def launch_client(self):
        exe = Path(self.get_config()['client_path'])
        if not exe.is_file() or exe.suffix.lower() != '.exe':
            messagebox.showerror('找不到客户端', '请在设置中保存正确的客户端程序路径。')
            return
        try:
            subprocess.Popen([str(exe), '--force-renderer-accessibility'], cwd=exe.parent)
            self.emit('已请求以辅助模式启动客户端；如果客户端原本正在运行，参数可能不生效。')
        except OSError as e:
            messagebox.showerror('启动失败', describe_error(e))

    def start_monitor(self):
        if self.jobs.busy:
            self.status.set('文件操作正在进行，请完成后启动监测。', state='warning')
            return
        if not self.get_config()['group']:
            self.emit_monitor('请先在设置 → 群聊监测中填写目标群完整名称。')
            self.open_settings()
            return
        if self.monitor_thread and self.monitor_thread.is_alive():
            self.emit_monitor('监测已运行，或正在停止中，请稍候。')
            return
        if not client_process(self.get_config()):
            self.emit_monitor('请先在设置 → 群聊监测中选择聊天客户端程序。')
            self.open_settings()
            return
        self.monitor_message = '正在连接聊天客户端'
        self.monitor_last_success = None
        self.monitor_requested = True
        self.monitor_success_mono = None
        self.health.start()
        self.monitor_stop = threading.Event()
        self.monitor_thread = threading.Thread(
            target=self.monitor, args=(self.get_config(), self.monitor_stop), daemon=True
        )
        self.monitor_thread.start()
        self.refresh_table()

    def stop_monitor(self):
        self.monitor_requested = False
        self.health.reset()
        if self.monitor_stop:
            self.monitor_stop.set()
        self.emit_monitor('已请求停止群聊监测；任务处理由“处理”菜单单独控制。')

    def monitor(self, config, stop):
        try:
            import uiautomation as auto

            with auto.UIAutomationInitializerInThread():
                last = None
                image_offset = 0
                while not stop.is_set() and not self.quit_event.is_set():
                    shots = []
                    image_status = ''

                    def capture(window, messages):
                        nonlocal shots, image_status, image_offset
                        from adapters.chat_images import capture_visible_qr

                        shots, image_status = capture_visible_qr(window, messages, offset=image_offset)
                        image_offset += 1

                    try:
                        texts = (
                            read_group(auto, config['group'], client_process(config), capture)
                            if config.get('capture_qr')
                            else read_group(auto, config['group'], client_process(config))
                        )
                    except Exception as e:
                        record_error(self.logger, 'monitor-read', e, limiter=self.error_limiter)
                        if describe_error(e) != last:
                            self.emit_monitor('读取异常：' + describe_error(e))
                            last = describe_error(e)
                        stop.wait(config['poll_seconds'])
                        continue
                    if stop.is_set():
                        break
                    urls, notices = capture_notifications(texts)
                    try:
                        n = self.store.add_many(urls, config['group'], notices=notices)
                        if n:
                            self.emit(f'发现 {n} 个新链接，已保存通知原文和任务。', state='success')
                            self.events.put(('new', n))
                        status = f'正常读取：本轮识别 {len(urls)} 个活动文档链接；已保存，重复链接合并到原任务。'
                        if config.get('capture_qr'):
                            from attachment_store import add_image

                            saved = 0
                            try:
                                for raw, message_id in shots:
                                    if stop.is_set() or self.quit_event.is_set():
                                        break
                                    _, new = add_image(self.store, raw, config['group'], message_id, qr_only=True)
                                    saved += int(new)
                            except Exception as error:
                                record_error(self.logger, 'monitor-image-save', error, limiter=self.error_limiter)
                                image_status = '图片保存失败：' + describe_error(error).splitlines()[0]
                            if saved:
                                self.emit(
                                    f'已保存 {saved} 张二维码图片，请从“更多 → 待关联图片”确认所属活动。',
                                    state='success',
                                )
                            status += ' ' + image_status
                        self.emit_monitor(status, True)
                        last = status
                    except Exception as e:
                        record_error(self.logger, 'monitor-link-save', e, limiter=self.error_limiter)
                        self.emit_monitor('链接保存失败，下轮将再次尝试：' + describe_error(e))
                    stop.wait(config['poll_seconds'])
        except Exception as e:
            record_error(self.logger, 'monitor-start', e, limiter=self.error_limiter)
            self.emit_monitor('监测未能运行：' + describe_error(e))

    def read_sheet_request(self, entries):
        try:
            request = self.sheet_requests.get_nowait()
        except queue.Empty:
            return
        try:
            from providers.tencent_sheet import read_header_region

            url = request['url']
            entry = entries.get(url)
            if request['cancel'].is_set() or time.monotonic() - request['created'] >= 120:
                raise ValueError('读取请求已取消或过期。')
            if not entry or entry['page'].is_closed():
                raise ValueError('所选表格尚未在助手浏览器打开，请重新排队打开后再读。')
            cancelled = lambda: (
                request['cancel'].is_set()
                or self.quit_event.is_set()
                or not self.processing.is_set()
                or not self.store.actionable(url)
            )
            if request.get('action', 'read') == 'write':
                from providers.tencent_sheet_fill import apply_sheet_plan

                if request.get('consumed'):
                    raise ValueError('本次确认已经使用，请重新读取并预览。')
                request['consumed'] = True
                result = apply_sheet_plan(
                    entry['page'],
                    url,
                    self.get_config(),
                    request['plan'],
                    request['approved'],
                    request['confirmed_layout'],
                    cancelled,
                )
                self.store.note(url, f"已写入并回读核验 {len(result['written'])} 格；报名是否完成请在网页核对。")
            elif request.get('action', 'read') == 'read':
                result = read_header_region(entry['page'], url, request['range'], cancelled)
            else:
                raise ValueError('未知表格操作。')
        except Exception as error:
            record_error(self.logger, 'sheet-header-read', error, limiter=self.error_limiter)
            result = {
                'error': describe_error(error)
                + (
                    ' 部分内容可能已即时保存，请在网页核对；不会自动重试。'
                    if request.get('action') == 'write' and request.get('consumed')
                    else ''
                )
            }
        try:
            request['reply'].put_nowait(result)
        except queue.Full:
            pass

    def browser(self):
        context = None
        active = None
        try:
            # Stale maintenance results must not launch even an empty browser.
            with self.config_lock:
                eligibility = queue_filter(self.config, self.manual_open_urls)
            if not self.processing.is_set() or not self.store.due(eligibility=eligibility):
                return
            from playwright.sync_api import sync_playwright

            with sync_playwright() as p:
                context = p.chromium.launch_persistent_context(
                    str(self.data / 'browser'),
                    channel='msedge',
                    headless=False,
                    no_viewport=True,
                    args=['--start-maximized'],
                )
                entries = {}
                capacity_reported = False
                while not self.quit_event.is_set():
                    config = self.get_config()
                    self.read_sheet_request(entries)
                    for url, entry in list(entries.items()):
                        page = entry['page']
                        if page.is_closed():
                            with self.preview_lock:
                                self.previews.pop(url, None)
                            self.store.note(url, '网页已关闭；是否已提交请自行核对，需要时可重新排队。')
                            del entries[url]
                            continue
                        if not self.processing.is_set():
                            continue
                        if not self.store.actionable(url):
                            continue
                        if not canonical_url(url):
                            from providers.tencent_sheet import is_tencent_sheet

                            status = (
                                '已打开'
                                + kind_label(url)
                                + (
                                    '；可使用表头读取预览，核对后确认写入。'
                                    if is_tencent_sheet(url)
                                    else '；此平台尚未启用自动填写，请在网页操作。'
                                )
                                + '在线表格输入后可能立即保存。'
                            )
                            if status != entry['status']:
                                self.store.note(url, status)
                                entry['status'] = status
                            continue
                        if canonical_url(page.url) != url:
                            continue
                        try:
                            plan = build_plan(page, config)
                            token = plan_signature(plan)
                            with self.preview_lock:
                                preview = self.previews.get(url)
                            if preview and preview['state'] == 'approved':
                                # Consume approval before writes; errors never reuse it.
                                with self.preview_lock:
                                    preview['state'] = 'applying'
                                try:
                                    n = apply_approved(page, url, config, preview['plan'], preview['approved'])
                                    with self.preview_lock:
                                        preview['state'] = 'applied'
                                    status = f'已按预览填写 {n} 项，请核对并手动提交。'
                                except Exception as e:
                                    record_error(self.logger, 'fill-approved', e, limiter=self.error_limiter)
                                    with self.preview_lock:
                                        self.previews.pop(url, None)
                                    status = '填写已停止：' + describe_error(e).splitlines()[0]
                            elif preview and preview['state'] == 'applied':
                                status = '本次预览已执行；剩余内容请在网页核对，或重新生成预览。'
                            elif plan and (not preview or preview['token'] != token):
                                with self.preview_lock:
                                    self.previews[url] = {'plan': plan, 'token': token, 'state': 'pending'}
                                status = '等待填写前预览确认：选中此任务，点击“填写前预览”。'
                                self.emit('表单已生成填写预览；确认前不会自动填字。', state='warning')
                            elif plan:
                                status = '等待填写前预览确认；登录或修改资料后预览会重新生成。'
                            else:
                                status = '尚未识别到题目；请先在浏览器登录，再查看填写预览。'
                        except Exception as error:
                            record_error(self.logger, 'form-preview', error, limiter=self.error_limiter)
                            status = '页面识别未完成，请在网页检查；尚未自动填写。'
                        if status != entry['status']:
                            self.store.note(url, status)
                            entry['status'] = status
                        try:
                            report = inspect_fields(page)
                            title = page.title()[:200]
                            signature = json.dumps([report, title], ensure_ascii=False)
                            if signature != entry.get('report_signature'):
                                self.store.report(url, report, title)
                                entry['report_signature'] = signature
                        except Exception as error:
                            record_error(self.logger, 'form-fields', error, limiter=self.error_limiter)
                            report = {
                                'missing': [],
                                'review': [],
                                'blocked': ['页面识别中断，字段提示可能过时，请在网页核对'],
                                'filled': 0,
                                'questions': 0,
                            }
                            if entry.get('report_signature') != 'unavailable':
                                self.store.report(url, report)
                                entry['report_signature'] = 'unavailable'

                    # Capacity is checked BEFORE claiming from the durable queue.
                    # Existing/restored form tabs also count toward the limit.
                    live_forms = sum(bool(canonical_task_url(pg.url)) for pg in context.pages if not pg.is_closed())
                    full = max(len(entries), live_forms) >= config['max_tabs']
                    with self.config_lock:
                        eligibility = queue_filter(self.config, self.manual_open_urls)
                    if (
                        full
                        and self.processing.is_set()
                        and self.store.due(eligibility=eligibility)
                        and not capacity_reported
                    ):
                        self.emit(
                            '已达到同时打开数量上限；后续任务已保存，关闭处理完的表单后自动继续。', state='warning'
                        )
                        capacity_reported = True
                    if not full:
                        capacity_reported = False
                    if self.processing.is_set() and not full:
                        with self.config_lock:
                            eligibility = queue_filter(self.config, self.manual_open_urls)
                        active = self.store.claim(eligibility=eligibility)
                        with self.config_lock:
                            manual_open = bool(active and active['url'] in self.manual_open_urls)
                            if active:
                                self.manual_open_urls.discard(active['url'])
                        if active:
                            url = active['url']
                            page = None
                            try:
                                if not self.processing.is_set() or (
                                    not manual_open and not auto_open_allowed(url, self.get_config())
                                ):
                                    self.store.release(url)
                                    if manual_open:
                                        with self.config_lock:
                                            self.manual_open_urls.add(url)
                                    active = None
                                    continue
                                if url in entries and not entries[url]['page'].is_closed():
                                    page = entries[url]['page']
                                    page.bring_to_front()
                                else:
                                    page = context.new_page()
                                    if not self.processing.is_set() or (
                                        not manual_open and not auto_open_allowed(url, self.get_config())
                                    ):
                                        page.close()
                                        self.store.release(url)
                                        if manual_open:
                                            with self.config_lock:
                                                self.manual_open_urls.add(url)
                                        active = None
                                        continue
                                    response = page.goto(url, wait_until='domcontentloaded', timeout=30000)
                                    if response and response.status >= 400:
                                        raise RuntimeError(f'网页返回 HTTP {response.status}')
                                    entries[url] = {'page': page, 'touched': set(), 'status': ''}
                                if self.quit_event.is_set():
                                    self.store.release(url)
                                else:
                                    self.store.opened(url)
                                    self.emit(
                                        '一个任务已打开；请在浏览器核对。在线表格输入后可能立即保存。'
                                        if not canonical_url(url)
                                        else '一个任务已打开；请在浏览器核对并自行提交。',
                                        state='warning',
                                    )
                            except Exception as e:
                                if page:
                                    try:
                                        page.close()
                                    except Exception:
                                        pass
                                entries.pop(url, None)
                                if self.quit_event.is_set():
                                    self.store.release(url)
                                else:
                                    record_error(self.logger, 'open-form', e)
                                    self.store.failed(
                                        url,
                                        describe_error(e).splitlines()[0],
                                        config['max_attempts'],
                                        config['retry_seconds'],
                                    )
                                    self.emit(
                                        open_failure_notice(
                                            self.store.task(url), self.get_config(), self.processing.is_set()
                                        ),
                                        state='error',
                                    )
                            active = None
                    if not context.pages:
                        self.processing.clear()
                        self.emit('助手浏览器已关闭，任务处理已暂停。点击“继续处理”可恢复。', state='warning')
                        break
                    context.pages[0].wait_for_timeout(700)
                context.close()
        except Exception as e:
            if active:
                try:
                    self.store.release(active['url'])
                except Exception:
                    pass
            record_error(self.logger, 'browser', e)
            self.browser_backoff = time.time() + 30
            self.emit(
                '浏览器无法继续运行，任务已保留；30秒后可再试。详情：' + describe_error(e).splitlines()[0],
                state='error',
            )
        finally:
            with self.preview_lock:
                self.previews.clear()
            try:
                while True:
                    request = self.sheet_requests.get_nowait()
                    request['cancel'].set()
                    request['reply'].put_nowait({'error': '助手浏览器已关闭，请重新打开表格后读取。'})
            except (queue.Empty, queue.Full):
                pass
            if context:
                try:
                    context.close()
                except Exception:
                    pass

    def close(self):
        if self.closing:
            return
        if self.close_window and self.close_window.winfo_exists():
            self.close_window.lift()
            return
        from close_dialog_ui import show_close_dialog

        show_close_dialog(self)

    def hide_to_tray(self):
        self.tray_hide_pending = True
        if self.tray and self.tray.ready.is_set():
            self.root.withdraw()
            self.tray_hide_pending = False
            return
        if self.tray and self.tray.thread.is_alive():
            return
        from tray_support import Tray

        self.tray = Tray(self.events)
        self.tray.start()
        self.emit('正在创建托盘图标，成功后隐藏助手窗口。', state='running')

    def handle_tray(self, event):
        kind = event[0]
        if self.closing:
            return
        if kind == 'tray_ready':
            if self.tray_hide_pending:
                self.root.withdraw()
                self.tray_hide_pending = False
        elif kind == 'tray_failed':
            self.tray_hide_pending = False
            self.root.deiconify()
            self.status.set('无法保持托盘运行：' + event[1], state='error')
        elif kind in ('tray_show', 'tray_exit'):
            self.tray_hide_pending = False
            self.root.deiconify()
            self.root.lift()
            if kind == 'tray_exit':
                self.close()
        elif kind == 'tray_pause':
            self.pause()
        elif kind == 'tray_resume':
            self.resume()

    def shutdown(self):
        if self.closing:
            return
        self.closing = True
        self.query.close()
        self.maintenance.close()
        try:
            self.jobs.close()
        except Exception as error:
            record_error(self.logger, 'shutdown-cleanup', error)
        self.quit_event.set()
        self.processing.clear()
        if self.tray:
            self.tray.stop()
        if self.monitor_stop:
            self.monitor_stop.set()
        self.root.deiconify()
        self.status.set('正在退出，等待后台任务保存完成。请勿强制结束程序。', state='running')
        self.root.protocol('WM_DELETE_WINDOW', lambda: None)
        self.root.configure(menu='')
        for child in self.root.winfo_children():
            if isinstance(child, tk.Toplevel):
                child.destroy()
            elif child.winfo_manager() == 'pack':
                child.pack_forget()
            elif child.winfo_manager() == 'grid':
                child.grid_remove()
        ttk.Label(self.root, text='正在安全退出…\n等待文件保存和后台任务结束，请勿强制结束程序。', padding=30).pack(
            expand=True
        )
        deadline = time.monotonic() + 40

        def finish():
            alive = any(t and t.is_alive() for t in (self.monitor_thread, self.browser_thread))
            try:
                self.jobs.poll()
                self.maintenance.poll()
            except Exception as error:
                record_error(self.logger, 'shutdown-cleanup', error)
            writing = any(
                t and t.is_alive()
                for t in (self.jobs.thread, self.backup_thread, self.query.jobs.thread, self.maintenance.thread)
            )
            if not writing and (not alive or time.monotonic() > deadline):
                self.root.destroy()
            else:
                self.root.after(200, finish)

        self.root.after(200, finish)


def main():
    main_started = time.perf_counter()
    if len(sys.argv) == 3 and sys.argv[1] == '--startup-probe':
        from selftest import run_startup_probe

        run_startup_probe(App, sys.argv[2], _IMPORT_STARTED, main_started)
        return
    from ui_layout import enable_high_dpi

    enable_high_dpi()
    if len(sys.argv) >= 2 and sys.argv[1] == '--purge-data':
        # Detached helper started after "清除全部数据并退出"; runs before the single-instance mutex.
        from data_purge import run_purge_helper

        sys.exit(run_purge_helper(sys.argv, ROOT, DATA))
    ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID('ChenZhuo.ActivityHelper')
    if len(sys.argv) == 3 and sys.argv[1] == '--self-test':
        from selftest import run_bundle_self_test

        run_bundle_self_test(App, ROOT, sys.argv[2])
        return
    # A second instance must not recover or claim another instance's active tasks/profile.
    import hashlib

    kernel = ctypes.WinDLL('kernel32', use_last_error=True)
    kernel.CreateMutexW.argtypes = [ctypes.c_void_p, wintypes.BOOL, wintypes.LPCWSTR]
    kernel.CreateMutexW.restype = wintypes.HANDLE
    key = hashlib.sha256(str(DATA.resolve()).lower().encode()).hexdigest()[:24]
    mutex = kernel.CreateMutexW(None, False, 'Local\\GroupFormHelper_' + key)
    duplicate = ctypes.get_last_error() == 183
    root = tk.Tk()
    if not mutex:
        root.withdraw()
        messagebox.showerror('无法启动', '无法建立单实例保护，请稍后重试。')
        root.destroy()
        return
    if duplicate:
        root.withdraw()
        messagebox.showinfo('助手已运行', '这个文件夹中的助手已在运行，请切回已有窗口。')
        root.destroy()
        return
    try:
        app = App(root)
    except Exception as e:
        root.withdraw()
        messagebox.showerror('无法启动', '无法读取或保存本地任务清单：' + describe_error(e))
        root.destroy()
        return
    try:
        root.mainloop()
    finally:
        kernel.CloseHandle.argtypes = [wintypes.HANDLE]
        kernel.CloseHandle(mutex)
    if getattr(app, 'purge_requested', False):
        from data_purge import launch_purge_helper

        launch_purge_helper(ROOT)


if __name__ == '__main__':
    if len(sys.argv) == 3 and sys.argv[1] == '--self-test':
        try:
            main()
        except Exception:
            import traceback

            Path(sys.argv[2]).write_text(
                json.dumps({'error': traceback.format_exc()}, ensure_ascii=False), encoding='utf-8'
            )
            sys.exit(1)
    else:
        main()
