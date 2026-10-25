"""Isolated diagnostics started from app.py (--startup-probe, --self-test). Temporary data only; no real chat client, profile or submission."""

import json
import queue
import sys
import time
from pathlib import Path
import tkinter as tk
from tkinter import ttk

from backup_support import make_backup, inspect_backup, stage_restore, apply_pending
from core import DEFAULTS, SAMPLE, fill_known_fields
from diagnostics import ErrorRateLimiter
from field_report import inspect_fields
from fill_preview import build_plan, apply_approved
from open_policy import queue_filter
from settings import SETTINGS, validate_settings
from storage_io import atomic_text
from task_store import TaskStore


def run_startup_probe(App, output, import_started, main_started):
    # Isolated diagnostic; no real database, browser session or chat monitoring.
    import tempfile
    from ui_layout import enable_high_dpi

    enable_high_dpi()
    with tempfile.TemporaryDirectory(prefix='助手启动探测-') as folder:
        start = time.perf_counter()
        root = tk.Tk()
        tk_finished = time.perf_counter()
        instance = None
        try:
            instance = App(root, folder)
            instance.processing.clear()
            instance.config.update(backup_enabled=False, notify_popup=False)
            init_finished = time.perf_counter()

            def ready():
                payload = {
                    'import_seconds': main_started - import_started,
                    'tk_seconds': tk_finished - start,
                    'app_init_seconds': init_finished - tk_finished,
                    'layout_seconds': time.perf_counter() - init_finished,
                    'runtime_to_ready_seconds': time.perf_counter() - import_started,
                    'mapped': bool(root.winfo_ismapped()),
                    'frozen': bool(getattr(sys, 'frozen', False)),
                }
                with atomic_text(Path(output)) as stream:
                    stream.write(json.dumps(payload, ensure_ascii=False))
                root.destroy()

            root.after_idle(lambda: root.after(50, ready))
            root.mainloop()
        finally:
            if instance is None or not instance.closing:
                root.destroy()


def run_bundle_self_test(App, root_dir, output):
    import tempfile
    import uiautomation as auto
    from playwright.sync_api import sync_playwright

    with tempfile.TemporaryDirectory() as tmp:
        store = TaskStore(Path(tmp) / 'tasks.sqlite3')
        store.add_many([SAMPLE], notices=[(SAMPLE, '活动通知\n测试原文')])
        assert store.claim()['url'] == SAMPLE
        recovered = TaskStore(Path(tmp) / 'tasks.sqlite3')
        recovered.recover_inflight()
        assert recovered.rows()[0]['state'] == 'pending'
        assert recovered.notices(SAMPLE)[0]['body'] == '活动通知\n测试原文'
        recovered.disposition([SAMPLE], 'ignored')
        assert not recovered.due()
        recovered.save_dates(SAMPLE, '2026-09-25 15:00', '', '2026-09-24')
        assert recovered.task(SAMPLE)['deadline'] == '2026-09-24'
        assert recovered.matching_urls('测试原文') == {SAMPLE}
        from attachment_store import add_image, associate, images, has_qr, get_image
        from PIL import Image
        import zxingcpp

        barcode = zxingcpp.create_barcode('local-self-test', zxingcpp.BarcodeFormat.QRCode)
        pixels = memoryview(zxingcpp.write_barcode_to_image(barcode, scale=4))
        qr = Image.frombytes('L', (pixels.shape[1], pixels.shape[0]), pixels.tobytes())
        key, new = add_image(recovered, qr)
        assert new and has_qr(get_image(recovered, key))
        associate(recovered, key, SAMPLE)
        backup = make_backup(recovered, Path(tmp), validate_settings(SETTINGS), 'manual')
        assert inspect_backup(backup, validate_settings)[3] == 1
        stage_restore(backup, Path(tmp), validate_settings)
        assert apply_pending(Path(tmp), validate_settings)
        assert images(TaskStore(Path(tmp) / 'tasks.sqlite3'), url=SAMPLE)[0]['id'] == key
        identity_store = TaskStore(Path(tmp) / 'tasks.sqlite3')
        first = 'https://docs.qq.com/sheet/LocalIdentityTest?tab=A&_t=1'
        second = 'https://docs.qq.com/sheet/LocalIdentityTest?tab=A&_t=2'
        assert identity_store.preview_additions([first, second])['new'] == 1
        assert identity_store.add_many([first, second], notices=[(first, '身份自检通知')]) == 1
        assert identity_store.task(second)['url'] == first
        assert identity_store.notices(first)[0]['body'] == '身份自检通知'
        limiter = ErrorRateLimiter(clock=lambda: 0)
        assert limiter.allow(('self-test',)) and not limiter.allow(('self-test',))
        from sheet_headers import parse_range, analyze_headers

        headers = analyze_headers(parse_range('A1:D2'), [['班级', '姓名', '学号', '联系方式'], ['234', '', '', '']], {})
        assert headers['suggested_row'] == 1 and headers['rows'][0]['columns'][3]['key'] is None
        from sheet_rows import assess_candidate_row

        candidate = assess_candidate_row(
            parse_range('A1:D3'), [['班级', '姓名', '学号', '联系方式'], ['234', '', '', ''], ['', '', '', '']], {}, 1
        )
        assert candidate['candidate_row'] == 3 and candidate['can_write'] is False
        from sheet_fill import make_sheet_plan, validate_approval

        local_plan = make_sheet_plan(
            {
                'bounds': parse_range('A1:D3'),
                'matrix': [['班级', '姓名', '学号', '联系方式'], ['234', '', '', ''], ['', '', '', '']],
                'worksheet': '工作表1',
                'document_title': '本地自检',
            },
            {'姓名': '测试同学'},
            1,
        )
        assert validate_approval(local_plan, {'B3': '测试同学'}) == {'B3': '测试同学'}
        test_root = tk.Tk()
        test_root.withdraw()
        test_app = App(test_root, Path(tmp) / 'ui')
        test_app.processing.clear()
        try:
            test_app.store.add_many([SAMPLE])
            test_app.refresh_table()
            until = time.monotonic() + 5
            while time.monotonic() < until and SAMPLE not in test_app.table.get_children():
                test_root.update()
                time.sleep(0.01)
            assert SAMPLE in test_app.table.get_children(), 'Asynchronous query did not render'
            test_app.config.update(auto_open_forms=False, auto_open_documents=False)
            test_app.processing.set()
            assert not test_app.store.due(eligibility=queue_filter(test_app.get_config()))
            test_app.browser()  # Must return before any browser is launched.
            assert test_app.store.state(SAMPLE) == 'pending'
            test_app.processing.clear()
            test_app.config['notify_popup'] = False
            test_app.deliver_alert('bundle-maintenance', '本地自检提醒', '仅使用临时数据')
            test_app.schedule_maintenance()
            until = time.monotonic() + 5
            while time.monotonic() < until and 'bundle-maintenance' in test_app.pending_alerts:
                test_root.update()
                time.sleep(0.01)
            assert 'bundle-maintenance' not in test_app.pending_alerts and test_app.store.unread_alert_count() == 1
            from appearance import apply_theme
            from design_tokens import PALETTES

            for theme in ('dark', 'light'):
                apply_theme(test_root, theme)
                test_app.status.set('本地界面状态自检', state='warning')
                assert ttk.Style(test_root).lookup('Status.warning.TLabel', 'foreground') == PALETTES[theme]['warning']
                assert 'Rounded.' in ttk.Style(test_root).layout('Primary.TButton')[0][0]
        finally:
            test_root.update_idletasks()
            test_root.destroy()
    with auto.UIAutomationInitializerInThread():
        auto.GetRootControl().Name
    from tray_support import Tray

    test_tray = Tray(queue.Queue())
    test_tray.start()
    try:
        assert test_tray.ready.wait(10)
    finally:
        test_tray.stop()
        test_tray.thread.join(5)
    assert not test_tray.thread.is_alive()
    test_profile = dict(DEFAULTS, 姓名='本地测试同学')
    with sync_playwright() as p:
        b = p.chromium.launch(channel='msedge', headless=True)
        page = b.new_page()
        page.set_content(
            '<div class="question-main-content"><div class="question-title">姓名</div><textarea></textarea></div>'
        )
        fill_known_fields(page, test_profile)
        assert page.locator('textarea').input_value() == test_profile['姓名']
        assert inspect_fields(page)['filled'] == 1
        page.set_content(
            '<div class="question-main-content"><div class="question-title">专业</div><textarea></textarea></div>'
        )
        fill_known_fields(page, dict(DEFAULTS, custom_fields={'专业': '水土保持'}))
        assert page.locator('textarea').input_value() == '水土保持'
        page.route(
            '**/*',
            lambda route: route.fulfill(
                content_type='text/html; charset=utf-8',
                body='<div class="question-main-content"><div class="question-title">姓名</div><textarea></textarea></div>',
            ),
        )
        page.goto(SAMPLE)
        plan = build_plan(page, test_profile)
        assert page.locator('textarea').input_value() == ''
        assert apply_approved(page, SAMPLE, test_profile, plan, {0: test_profile['姓名']}) == 1
        b.close()
    from data_cleanup import CleanupOptions, preview_cleanup, execute_cleanup

    with tempfile.TemporaryDirectory() as clean_tmp:
        clean_data = Path(clean_tmp)
        clean_store = TaskStore(clean_data / 'tasks.sqlite3')
        clean_store.add_many([SAMPLE])
        clean_store.done([SAMPLE])
        with clean_store.connection() as db:
            db.execute('UPDATE tasks SET updated=?', (time.time() - 100 * 86400,))
        plan = preview_cleanup(clean_store, clean_data, CleanupOptions(tasks=True))
        cleaned = execute_cleanup(clean_store, clean_data, validate_settings({}), plan)
        assert cleaned['tasks'] == 1 and clean_store.task(SAMPLE) is None and cleaned['safety']
    from notice_tools import parse_import

    assert parse_import('完整通知', SAMPLE)[0] == [SAMPLE]
    from about_ui import read_document, resource_root

    assert 'github.com/cz-303/group-activity-form-helper/issues' in read_document('软件声明')
    assert '提交前' in read_document('隐私说明')
    assert any((resource_root() / 'third_party' / 'licenses' / 'pystray').glob('*COPYING.LGPL'))
    assert (Path(getattr(sys, '_MEIPASS', root_dir)) / 'activity-helper.ico').is_file()
    Path(output).write_text(
        json.dumps(
            {
                'bundle': 'ok',
                'tasks_recovery': 'ok',
                'notice_storage': 'ok',
                'ignore': 'ok',
                'gui': 'ok',
                'async_query': 'ok',
                'maintenance': 'ok',
                'fields': 'ok',
                'dates': 'ok',
                'search': 'ok',
                'tray': 'ok',
                'custom_profile': 'ok',
                'backup_restore': 'ok',
                'theme': 'ok',
                'fill_preview': 'ok',
                'notice_import': 'ok',
                'brand_icon': 'ok',
                'data_cleanup': 'ok',
                'image_backup_restore': 'ok',
                'qr_decoder': 'ok',
                'auto_open_policy': 'ok',
                'link_identity': 'ok',
                'log_throttle': 'ok',
            }
        ),
        encoding='utf-8',
    )
