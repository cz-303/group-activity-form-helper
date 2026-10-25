"""Keep chat display text while identifying its observed message content separately."""

import re

_STAMP = re.compile(
    r'(?:(?:昨天|前天|今天|星期[一二三四五六日天]|周[一二三四五六日天]|\d{4}[/.-]\d{1,2}[/.-]\d{1,2})\s+)?(?:[01]?\d|2[0-3]):[0-5]\d'
)


def is_timestamp(text):
    return bool(_STAMP.fullmatch(text.strip()))


class ChatMessage(str):
    """String compatibility preserves the existing reader and notice tuple API.

    Only the reader constructs this after recognizing separate UI metadata and
    content regions. Plain imported text never receives metadata heuristics.
    """

    def __new__(cls, raw, content):
        value = super().__new__(cls, raw)
        value.content = content
        return value

    def matches_legacy(self, raw):
        # Old versions saved UI decorations as part of the raw text. Match only
        # a leading timestamp. Old arbitrary sender-like lines have no structural
        # provenance, so keep them significant rather than hide a possible edit.
        if raw == self.content:
            return True
        first, separator, rest = raw.partition('\n')
        if separator and is_timestamp(first):
            raw = rest
        if raw == self.content:
            return True
        return False
