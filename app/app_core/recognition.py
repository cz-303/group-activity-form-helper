"""All recognizers run; conflicts and failures are values, not UI exceptions."""

import logging
import math
from typing import Protocol
from .models import InputMessage, LinkCandidate, LinkInfo, RecognitionResult
from .url_candidates import extract_candidates, parse_url

logger = logging.getLogger(__name__)


class LinkRecognizer(Protocol):
    id: str

    def recognize(self, candidate: LinkCandidate) -> LinkInfo | None: ...


class RecognitionEngine:
    def __init__(self, recognizers):
        self.recognizers = tuple(recognizers)
        ids = [r.id for r in self.recognizers]
        if any(not isinstance(i, str) or not i for i in ids) or len(set(ids)) != len(ids):
            raise ValueError('识别器标识必须非空且唯一。')

    @staticmethod
    def _validate(info, candidate, recognizer):
        if not isinstance(info, LinkInfo) or info.raw_url != candidate.raw_url or info.recognizer_id != recognizer.id:
            raise ValueError('识别结果与输入不一致。')
        if (
            type(info.confidence) not in (int, float)
            or not math.isfinite(info.confidence)
            or not 0 <= info.confidence <= 1
        ):
            raise ValueError('识别置信度无效。')
        for value in (info.scheme, info.kind, info.identity_key, info.reason):
            if not isinstance(value, str) or not value:
                raise ValueError('识别结果缺少必要字段。')
        if info.provider_id is not None and (not isinstance(info.provider_id, str) or not info.provider_id):
            raise ValueError('平台标识无效。')
        if parse_url(info.normalized_url).scheme != info.scheme:
            raise ValueError('识别结果的协议不一致。')

    def recognize(self, candidate):
        if not isinstance(candidate, LinkCandidate):
            return RecognitionResult(None, 'invalid', diagnostics=('链接候选类型不正确。',))
        try:
            parse_url(candidate.raw_url)
        except ValueError as exc:
            return RecognitionResult(candidate, 'invalid', diagnostics=(str(exc),))
        matches, diagnostics = [], []
        for recognizer in self.recognizers:
            try:
                info = recognizer.recognize(candidate)
                if info is not None:
                    self._validate(info, candidate, recognizer)
                    matches.append(info)
            except Exception as exc:
                # Do not log URLs, message bodies or exception text containing user data.
                logger.warning('Recognizer %s failed (%s)', recognizer.id, type(exc).__name__)
                diagnostics.append(f'识别器 {recognizer.id} 暂时不可用，其他识别结果仍保留。')
        matches.sort(key=lambda m: (-m.confidence, m.recognizer_id))
        specific = [m for m in matches if m.provider_id is not None]
        choices = specific or matches
        identities = {(m.provider_id, m.kind, m.identity_key) for m in choices}
        if len(identities) > 1:
            status, selected = 'ambiguous', None
        elif choices:
            status, selected = ('recognized' if specific else 'generic'), choices[0]
        else:
            status, selected = ('error' if diagnostics else 'unknown'), None
        return RecognitionResult(candidate, status, selected, tuple(matches), tuple(diagnostics))

    def recognize_text(self, text):
        try:
            candidates = extract_candidates(text)
        except ValueError as exc:
            return (RecognitionResult(None, 'invalid', diagnostics=(str(exc),)),)
        return tuple(self.recognize(candidate) for candidate in candidates)

    def recognize_message(self, message):
        if not isinstance(message, InputMessage):
            return (RecognitionResult(None, 'invalid', diagnostics=('通知类型不正确。',)),)
        return self.recognize_text(message.text)
