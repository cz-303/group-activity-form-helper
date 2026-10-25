"""Opening preferences do not grant permission to fill or submit a document."""

from task_links import canonical_task_url
from providers.tencent_docs import canonical_form_url

OPEN_KEYS = ('auto_open_forms', 'auto_open_documents')


def open_failure_notice(task, config, processing=True):
    if not task or task['state'] != 'failed':
        return '打开未完成，请查看任务的最新状态。'
    if task['disposition'] != 'active':
        return '打开失败，任务已保留；忽略或暂缓期间不会重试。'
    if task['retry_at'] is None:
        return '打开失败，已达到尝试上限；任务已保留，需要时请手动重新排队。'
    if not auto_open_allowed(task['url'], config):
        return '打开失败，任务已保留；此类链接自动跳转已关闭，请手动重新排队打开。'
    if not processing:
        return '打开失败，任务已保留；处理已暂停，恢复处理后按设置重试。'
    return '打开失败，任务已保留；到达重试时间且处理开启时将再次尝试。'


def auto_open_allowed(url, config):
    if not canonical_task_url(url):
        return False
    key = 'auto_open_forms' if canonical_form_url(url) else 'auto_open_documents'
    return config.get(key, True) is True


def queue_filter(config, manual_urls=()):
    """Internal parameterized SQL; URLs have passed task admission validation."""
    parts = []
    parameters = []
    forms = config.get('auto_open_forms', True) is True
    documents = config.get('auto_open_documents', True) is True
    if forms and documents:
        return '1', ()
    if forms:
        parts.append("url LIKE 'https://docs.qq.com/form/page/%'")
    if documents:
        parts.append("(url LIKE 'https://docs.qq.com/sheet/%' OR url LIKE 'https://www.kdocs.cn/l/%')")
    urls = tuple(sorted(set(manual_urls)))
    if urls:
        parts.append('url IN (' + ','.join('?' for _ in urls) + ')')
        parameters.extend(urls)
    return (' OR '.join(parts) or '0'), tuple(parameters)
