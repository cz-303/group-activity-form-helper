"""Cross-day chat-client presentation changes must not become activity updates."""
import sys
import json
import tempfile
import unittest
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from core import capture_notifications
from chat_reader import read_group_windows
from task_store import TaskStore
from reminders import due_alerts
from backup_support import make_backup, inspect_backup
from settings import validate_settings
from test_reader import Node, window, G
from notice_identity import ChatMessage

URL = 'https://docs.qq.com/form/page/CrossDayLocalOnly'
CONTENT = '【腾讯文档】测试\n\n' + URL


def message(stamp='昨天 15:22', sender='测试发送者', content=CONTENT):
    w, _ = window()
    row = w.kids[2].kids[0].kids[0].kids[0]
    row.kids = [Node(kids=[Node(stamp, 'TextControl')]), Node(kids=[
        Node(sender, 'GroupControl'), Node(kids=[Node(content, 'TextControl')])])]
    return read_group_windows([w], G, lambda _: False)[0]


def actual_row():
    shape = json.loads((Path(__file__).parent / 'fixtures/chat_notice_shape_20261023.json').read_text(encoding='utf-8'))
    def build(item):
        category = item['name_category']
        name = ('' if category == 'empty' else '2026/10/16 15:22' if category == 'timestamp'
                else URL if category == 'test-form-link' else
                '【腾讯文档】测试\n' if item['kind'] == 'TextControl' else '测试发送者')
        return Node(name, item['kind'], kids=[build(child) for child in item['children']])
    w, _ = window()
    row = build(shape)
    row.AutomationId = '123'
    w.kids[2].kids[0].kids[0].kids[0] = row
    return w, row


class NoticeMetadata(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.path = Path(self.temp.name) / 'db.sqlite3'
        self.store = TaskStore(self.path)

    def tearDown(self):
        self.temp.cleanup()

    def save(self, text):
        urls, notices = capture_notifications([text])
        self.store.add_many(urls, G, notices)

    def test_old_raw_notice_matches_cross_day_and_sender_decoration(self):
        self.save('15:22\n' + CONTENT)
        self.save(message())
        self.assertEqual(self.store.task(URL)['notice_revision'], 0)
        self.assertEqual(len(self.store.notices(URL)), 1)
        self.assertEqual(self.store.notices(URL)[0]['body'], '15:22\n' + CONTENT)

    def test_sender_and_timestamp_changes_stay_duplicate_after_restart(self):
        self.save(message('15:22', '原昵称'))
        self.store = TaskStore(self.path)
        self.save(message('昨天 15:22', '新昵称'))
        self.save(message('星期五 15:22', '新昵称'))
        self.save(message('2026/10/16 15:22', '新昵称'))
        self.assertEqual(self.store.task(URL)['notice_revision'], 0)
        self.assertEqual(len(self.store.notices(URL)), 1)

    def test_real_content_change_with_same_metadata_still_alerts_once(self):
        self.save(message())
        changed = message(content='地点改为东门\n' + CONTENT)
        self.save(changed)
        self.save(changed)
        self.assertEqual(self.store.task(URL)['notice_revision'], 1)
        self.assertEqual(len(self.store.notices(URL)), 2)
        config = {'notify_updates': True, 'deadline_reminders': False}
        self.assertEqual(len(due_alerts(self.store, config)), 1)
        self.assertEqual(due_alerts(self.store, config), [])

    def test_time_like_text_inside_content_is_not_discarded(self):
        self.save(message(content='15:22\n' + CONTENT))
        self.save(message(content='昨天 15:22\n' + CONTENT))
        self.assertEqual(self.store.task(URL)['notice_revision'], 1)

    def test_sender_name_inside_content_is_not_discarded(self):
        self.save(message(content='测试发送者\n' + CONTENT))
        self.save(message(content=CONTENT))
        self.assertEqual(self.store.task(URL)['notice_revision'], 1)

    def test_manual_notice_prefix_is_not_treated_as_ui_metadata(self):
        self.save('15:22\n' + CONTENT)
        self.save('昨天 15:22\n' + CONTENT)
        self.assertEqual(self.store.task(URL)['notice_revision'], 1)

    def test_legacy_sender_like_authored_line_is_not_silently_removed(self):
        self.save('15:22\n测试发送者\n' + CONTENT)
        self.save(message())
        self.assertEqual(self.store.task(URL)['notice_revision'], 1)

    def test_legacy_identity_adoption_keeps_task_state_and_original_record(self):
        self.save('15:22\n' + CONTENT)
        self.store.done([URL])
        before = self.store.task(URL)
        original = self.store.notices(URL)[0]
        self.save(message())
        after = self.store.task(URL)
        self.assertEqual(after, before)
        notice = self.store.notices(URL)[0]
        self.assertNotEqual(notice['id'], original['id'])
        for key in ('url', 'source', 'captured', 'body'):
            self.assertEqual(notice[key], original[key])

    def test_backup_roundtrip_retains_stable_identity(self):
        self.save(message('15:22', '原昵称'))
        archive = make_backup(self.store, Path(self.temp.name), validate_settings({}), 'manual')
        _, payload, _, count = inspect_backup(archive, validate_settings)
        self.assertEqual(count, 1)
        restored = Path(self.temp.name) / 'restored.sqlite3'
        restored.write_bytes(payload['tasks.sqlite3'])
        self.store = TaskStore(restored)
        self.save(message('昨天 15:22', '新昵称'))
        self.assertEqual(self.store.task(URL)['notice_revision'], 0)
        self.assertEqual(len(self.store.notices(URL)), 1)

    def test_ambiguous_row_preserves_all_text_and_still_reports_changes(self):
        w, _ = window()
        row = w.kids[2].kids[0].kids[0].kids[0]
        row.kids = [Node('15:22', 'TextControl'), Node(CONTENT, 'TextControl')]
        text = read_group_windows([w], G, lambda _: False)[0]
        self.assertEqual(text, '15:22\n' + CONTENT)
        self.save(text)
        row.kids[0].Name = '昨天 15:22'
        self.save(read_group_windows([w], G, lambda _: False)[0])
        self.assertEqual(self.store.task(URL)['notice_revision'], 1)

    def test_generic_chromium_container_kinds_keep_identity(self):
        self.save('15:22\n' + CONTENT)
        for kind in ('PaneControl', 'CustomControl'):
            w, _ = window()
            row = w.kids[2].kids[0].kids[0].kids[0]
            row.kids = [Node(kind=kind, kids=[Node('昨天 15:22', 'TextControl')]),
                Node(kind=kind, kids=[Node('测试发送者', kind),
                    Node(kind=kind, kids=[Node(CONTENT, 'TextControl')])])]
            self.save(read_group_windows([w], G, lambda _: False)[0])
        self.assertEqual(self.store.task(URL)['notice_revision'], 0)

    def test_transparent_outer_wrapper_keeps_identity(self):
        self.save('15:22\n' + CONTENT)
        w, _ = window()
        row = w.kids[2].kids[0].kids[0].kids[0]
        row.kids = [Node(kind='CustomControl', kids=[
            Node(kids=[Node('昨天 15:22', 'TextControl')]), Node(kids=[
                Node('测试发送者'), Node(kids=[Node(CONTENT, 'TextControl')])])])]
        self.save(read_group_windows([w], G, lambda _: False)[0])
        self.assertEqual(self.store.task(URL)['notice_revision'], 0)

    def test_actual_client_avatar_and_empty_layout_nodes_keep_identity(self):
        self.save('15:22\n' + CONTENT)
        w, _ = actual_row()
        text = read_group_windows([w], G, lambda _: False)[0]
        self.assertIsInstance(text, ChatMessage)
        self.assertEqual(text.content, CONTENT)
        self.assertIn('测试发送者', text)
        self.save(text)
        self.assertEqual(self.store.task(URL)['notice_revision'], 0)
        self.assertEqual(len(self.store.notices(URL)), 1)

    def test_avatar_with_additional_visible_text_is_ambiguous(self):
        w, row = actual_row()
        row.kids[0].kids[1].kids[0].kids[0].Name = '另一个有意义的文字'
        text = read_group_windows([w], G, lambda _: False)[0]
        self.assertNotIsInstance(text, ChatMessage)
        self.assertIn('另一个有意义的文字', text)

    def test_unnamed_interactive_control_is_not_an_empty_layout_node(self):
        w, row = actual_row()
        row.kids[0].kids[1].kids[1].ControlTypeName = 'ButtonControl'
        text = read_group_windows([w], G, lambda _: False)[0]
        self.assertNotIsInstance(text, ChatMessage)


if __name__ == '__main__':
    unittest.main()
