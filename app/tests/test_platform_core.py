"""Offline contract and compatibility tests; no live chat client or network access."""
import ast
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path
import random
import sys
import tempfile
import unittest

SOURCE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SOURCE))

from app_core.models import InputMessage, LinkCandidate, LinkInfo
from app_core.recognition import RecognitionEngine
from app_core.url_candidates import extract_candidates
from bootstrap import create_recognition_engine
from core import canonical_url, capture_notifications, links_from_text
from notice_tools import parse_import
from providers.generic_links import GenericWebRecognizer
from task_store import TaskStore

FORM = 'https://docs.qq.com/form/page/ContractTest'


class FixedRecognizer:
    def __init__(self, ident, provider='test', score=0.8, key='item'):
        self.id, self.provider, self.score, self.key = ident, provider, score, key
        self.calls = 0

    def recognize(self, candidate):
        self.calls += 1
        return LinkInfo(candidate.raw_url, candidate.raw_url, 'https', self.id,
                        self.provider, 'form', self.key, self.score, 'test rule')


class BrokenRecognizer:
    id = 'broken'

    def recognize(self, candidate):
        raise RuntimeError('PRIVATE_URL_AND_PERSONAL_DATA')


class RecognitionContracts(unittest.TestCase):
    def setUp(self):
        self.engine = create_recognition_engine()

    def test_source_is_independent_of_target(self):
        for source in ('chat', 'manual', 'future_source'):
            message = InputMessage(source, '测试来源', None, FORM, datetime.now(timezone.utc))
            result = self.engine.recognize_message(message)[0]
            self.assertEqual(result.selected.provider_id, 'tencent_docs')
            self.assertEqual(result.status, 'recognized')
            self.assertEqual(len(result.matches), 2)

    def test_http_unicode_query_fragment_preserved(self):
        raw = 'http://example.test/活动?ticket=001&x=2#section'
        result = self.engine.recognize(LinkCandidate(raw))
        self.assertEqual(result.status, 'generic')
        self.assertEqual(result.selected.normalized_url, raw)
        self.assertIsNone(result.selected.provider_id)

    def test_different_generic_parameters_are_not_merged(self):
        results = self.engine.recognize_text('https://x.test/?id=1 https://x.test/?id=2')
        self.assertNotEqual(results[0].selected.identity_key, results[1].selected.identity_key)

    def test_opaque_and_hierarchical_schemes_are_data_only(self):
        for raw in ('campus://event/12?token=x', 'campus:event:12', 'javascript:alert(1)', 'file:///C:/test'):
            with self.subTest(raw=raw):
                info = self.engine.recognize(LinkCandidate(raw)).selected
                self.assertEqual(info.kind, 'scheme')
                self.assertIsNone(info.provider_id)
                self.assertIsNone(canonical_url(raw))
                self.assertEqual(links_from_text(raw), [])

    def test_candidates_keep_offsets_balanced_brackets_and_chinese_prefix(self):
        text = '通知https://x.test/a(b)，下一项 campus:event-1。'
        candidates = extract_candidates(text)
        self.assertEqual([c.raw_url for c in candidates], ['https://x.test/a(b)', 'campus:event-1'])
        for c in candidates:
            self.assertEqual(text[c.start:c.end], c.raw_url)

    def test_multiple_candidates_fail_independently(self):
        results = self.engine.recognize_text('https://x.test:bad/path ' + FORM + ' campus:item')
        self.assertEqual([r.status for r in results], ['invalid', 'recognized', 'generic'])

    def test_empty_unknown_and_wrong_input(self):
        self.assertEqual(self.engine.recognize_text('没有链接 😀'), ())
        self.assertEqual(self.engine.recognize_text(''), ())
        self.assertEqual(self.engine.recognize_text(None)[0].status, 'invalid')
        self.assertEqual(self.engine.recognize_message(None)[0].status, 'invalid')
        self.assertEqual(self.engine.recognize(None).status, 'invalid')
        self.assertEqual(RecognitionEngine([]).recognize(LinkCandidate('campus:item')).status, 'unknown')

    def test_invalid_urls(self):
        for raw in ('https://', 'https://[bad', 'https://user:pass@x.test', 'https://x.test:99999',
                    'https://x.test/a\nX', 'https://x.test/\x00', 'C:\\test', 'campus:', 42):
            with self.subTest(raw=raw):
                self.assertEqual(self.engine.recognize(LinkCandidate(raw)).status, 'invalid')

    def test_bounded_input_reports_failure_not_partial_success(self):
        self.assertEqual(self.engine.recognize_text('x' * 500001)[0].status, 'invalid')
        self.assertEqual(self.engine.recognize_text('https://x.test ' * 1001)[0].status, 'invalid')
        self.assertEqual(self.engine.recognize(LinkCandidate('https://x.test/' + 'a' * 8192)).status, 'invalid')

    def test_all_recognizers_run_and_specific_beats_generic(self):
        first, second = FixedRecognizer('first'), FixedRecognizer('second')
        engine = RecognitionEngine([GenericWebRecognizer(), first, second])
        result = engine.recognize(LinkCandidate('https://x.test'))
        self.assertEqual((first.calls, second.calls), (1, 1))
        self.assertEqual(result.status, 'recognized')
        self.assertEqual(result.selected.provider_id, 'test')
        self.assertEqual(len(result.matches), 3)

    def test_conflicts_are_not_resolved_by_high_confidence(self):
        result = RecognitionEngine([FixedRecognizer('a', 'one', 1), FixedRecognizer('b', 'two', .2)]).recognize(LinkCandidate('https://x.test'))
        self.assertEqual(result.status, 'ambiguous')
        self.assertIsNone(result.selected)

    def test_tie_is_registration_order_independent(self):
        a, b = FixedRecognizer('a'), FixedRecognizer('b')
        one = RecognitionEngine([a, b]).recognize(LinkCandidate('https://x.test'))
        two = RecognitionEngine([b, a]).recognize(LinkCandidate('https://x.test'))
        self.assertEqual(one, two)

    def test_failure_isolated_and_diagnostics_redacted(self):
        with self.assertLogs('app_core.recognition', level='WARNING') as logs:
            result = RecognitionEngine([BrokenRecognizer(), GenericWebRecognizer()]).recognize(LinkCandidate('https://x.test/private'))
        self.assertEqual(result.status, 'generic')
        self.assertTrue(result.diagnostics)
        self.assertNotIn('PRIVATE', str(result.diagnostics) + str(logs.output))
        self.assertNotIn('https://x.test', str(logs.output))

    def test_only_broken_recognizer_returns_error(self):
        with self.assertLogs('app_core.recognition'):
            result = RecognitionEngine([BrokenRecognizer()]).recognize(LinkCandidate('https://x.test'))
        self.assertEqual(result.status, 'error')

    def test_invalid_provider_results_rejected(self):
        base = FixedRecognizer('bad')
        for changes in ({'confidence': float('nan')}, {'confidence': float('inf')}, {'confidence': -1},
                        {'confidence': True}, {'raw_url': 'https://other.test'}, {'scheme': 'http'},
                        {'provider_id': ''}, {'identity_key': ''}, {'normalized_url': 'https://'}):
            with self.subTest(changes=changes):
                class Malformed:
                    id = 'bad'
                    def recognize(self, candidate):
                        return replace(base.recognize(candidate), **changes)
                with self.assertLogs('app_core.recognition'):
                    result = RecognitionEngine([Malformed()]).recognize(LinkCandidate('https://x.test'))
                self.assertEqual(result.status, 'error')

    def test_duplicate_registration_rejected(self):
        with self.assertRaises(ValueError):
            RecognitionEngine([FixedRecognizer('same'), FixedRecognizer('same')])

    def test_future_platform_requires_registration_only(self):
        engine = RecognitionEngine([FixedRecognizer('campus.form', 'future_campus')])
        self.assertEqual(engine.recognize(LinkCandidate('https://x.test')).selected.provider_id, 'future_campus')

    def test_random_untrusted_text_does_not_raise(self):
        rng = random.Random(42)
        alphabet = 'aZ中😀:/?[]()@\\\x00\n %012'
        for _ in range(300):
            text = ''.join(rng.choice(alphabet) for _ in range(rng.randrange(200)))
            self.engine.recognize_text(text)

    def test_core_import_boundary(self):
        allowed = {'dataclasses', 'datetime', 'typing', 'logging', 'math', 're', 'urllib'}
        for path in (SOURCE / 'app_core').glob('*.py'):
            for node in ast.walk(ast.parse(path.read_text(encoding='utf-8'))):
                if isinstance(node, ast.Import):
                    self.assertTrue(all(n.name.split('.')[0] in allowed for n in node.names), path.name)
                elif isinstance(node, ast.ImportFrom) and not node.level:
                    self.assertIn(node.module.split('.')[0], allowed, path.name)


class LegacyCompatibility(unittest.TestCase):
    def test_existing_canonical_identity(self):
        for raw in (FORM, FORM + '/', FORM + '?x=1#fragment', '  ' + FORM + '  ',
                    FORM.replace('docs.qq.com', 'DOCS.QQ.COM:443')):
            self.assertEqual(canonical_url(raw), FORM)
        for raw in ('http://docs.qq.com/form/page/a', FORM.replace('docs.qq.com', 'docs.qq.com.evil.test'),
                    FORM.replace('docs.qq.com', 'user@docs.qq.com'), 'https://docs.qq.com/sheet/a'):
            self.assertIsNone(canonical_url(raw))

    def test_existing_notice_capture(self):
        body = '活动😀（' + FORM + '?x=1），请报名！\n' + FORM + '\nhttp://example.test campus:item'
        self.assertEqual(links_from_text(body), [FORM])
        urls, notices = capture_notifications([body, FORM])
        self.assertEqual(urls, [FORM])
        self.assertEqual(notices, [(FORM, body), (FORM, FORM)])

    def test_long_legacy_query_still_uses_existing_identity(self):
        self.assertEqual(links_from_text(FORM + '?x=' + 'a' * 10000), [FORM])
        long_id = FORM + 'a' * 9000
        self.assertEqual(links_from_text(long_id), [long_id])

    def test_manual_import_still_only_queues_existing_provider(self):
        with self.assertRaises(ValueError):
            parse_import('https://example.test campus:item')
        self.assertEqual(parse_import('原文 ' + FORM + '\nhttps://example.test')[0], [FORM])

    def test_persistence_and_done_survive_recapture(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'tasks.sqlite3'
            store = TaskStore(path)
            urls, notices = capture_notifications(['原通知 ' + FORM])
            store.add_many(urls, '测试群', notices)
            store.done(urls)
            store.add_many(urls, source='测试群', notices=notices)
            restored = TaskStore(path)
            self.assertEqual(restored.state(FORM), 'done')
            self.assertEqual(len(restored.notices(FORM)), 1)
            with self.assertRaises(ValueError):
                restored.add_many(['https://example.test'])


if __name__ == '__main__':
    unittest.main()
