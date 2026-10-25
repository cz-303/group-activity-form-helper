"""Admission to the task list is separate from authorization to fill a page."""

from app_core.models import LinkCandidate
from app_core.url_candidates import MAX_URL_LENGTH
from providers.document_links import DocumentRecognizer
from providers.tencent_docs import canonical_form_url, legacy_candidates


def canonical_task_url(raw):
    form = canonical_form_url(raw)
    if form:
        return form
    if not isinstance(raw, str) or len(raw) > MAX_URL_LENGTH:
        return None
    result = DocumentRecognizer().recognize(LinkCandidate(raw.strip()))
    return result.normalized_url if result else None


def task_identity(raw):
    form = canonical_form_url(raw)
    if form:
        return 'tencent_docs:' + form
    if not isinstance(raw, str) or len(raw) > MAX_URL_LENGTH:
        return None
    result = DocumentRecognizer().recognize(LinkCandidate(raw.strip()))
    return result.identity_key if result else None


def task_links_from_text(text):
    return sorted({url for item in legacy_candidates(text) if (url := canonical_task_url(item.raw_url))})


def kind_label(url):
    if canonical_form_url(url):
        return '腾讯收集表'
    result = DocumentRecognizer().recognize(LinkCandidate(url))
    return '腾讯在线表格' if result and result.provider_id == 'tencent_docs' else '金山文档' if result else '未知链接'
