"""Local-only fixed-profile Tencent Forms filler; never submits a form."""

import re
from bootstrap import canonical_legacy_url, legacy_links_from_text

DEFAULTS = {
    "group": "",
    "姓名": "",
    "学号": "",
    "班级": "",
    "学院": "",
    "联系电话": "",
    "keywords": "活动,报名,招募,收集表,讲座,宣讲",
    "client_path": "",
}
SAMPLE = "https://docs.qq.com/form/page/LocalTestOnly"
ALIASES = {
    "姓名": "姓名",
    "学生姓名": "姓名",
    "你的姓名": "姓名",
    "您的姓名": "姓名",
    "学号": "学号",
    "学生学号": "学号",
    "班级": "班级",
    "所在班级": "班级",
    "学院": "学院",
    "所在学院": "学院",
    "联系电话": "联系电话",
    "手机号": "联系电话",
    "手机号码": "联系电话",
    "联系方式（手机号）": "联系电话",
}


def canonical_url(raw):
    """Compatibility entry point; only the existing Tencent form type is executable."""
    return canonical_legacy_url(raw)


def links_from_text(text):
    return legacy_links_from_text(text)


def field_key(title):
    return ALIASES.get(re.sub(r"[\s*＊:：]+", "", title))


def normalize_title(title):
    return re.sub(r"[\s*＊:：]+", "", title)


def validate_custom_fields(fields):
    if not isinstance(fields, dict) or len(fields) > 50:
        raise ValueError('自定义资料最多50项，请使用题目名称与填写内容。')
    result = {}
    seen = set()
    for raw, value in fields.items():
        if not isinstance(raw, str) or not isinstance(value, str):
            raise ValueError('资料名称与内容必须是文字。')
        title = raw.strip()
        key = normalize_title(title)
        if not key or len(title) > 80 or len(value) > 4000:
            raise ValueError('名称不能为空且最多80字，内容最多4000字。')
        if key in ALIASES or key in seen:
            raise ValueError('题目名称重复或与已有资料重名：' + title)
        if re.search('密码|验证码|口令|密钥|令牌', key):
            raise ValueError('密码、验证码等验证信息不支持自动填写。')
        seen.add(key)
        result[title] = value.strip()
    return result


def capture_notifications(texts):
    from task_links import task_links_from_text

    notices = [(url, text) for text in texts for url in task_links_from_text(text)]
    return sorted({url for url, _ in notices}), notices


def matching_window(title, group, client=''):
    # Only a dedicated group window is accepted; seeing a group in the sidebar is insufficient.
    # A trailing " - <client name>" is allowed only for the client the user selected.
    suffix = r'(?:\s*[-—–]\s*' + re.escape(client) + ')?' if client else ''
    return bool(re.fullmatch(re.escape(group) + r'(?:\s*[（(]\d+[）)])?' + suffix, title.strip()))


def fill_known_fields(page, profile, touched=None):
    """Return a status without guessing order, clearing answers, clicking, or submitting."""
    touched = touched if touched is not None else set()
    cards = page.locator('.question-main-content')
    count = cards.count()
    if not count:
        return "尚未识别到收集表题目；如需登录，请在浏览器中自行登录。"
    titles = []
    for i in range(count):
        t = cards.nth(i).locator('.question-title')
        titles.append(t.inner_text().strip() if t.count() == 1 else "")
    custom = {
        normalize_title(k): ('custom:' + normalize_title(k), v)
        for k, v in validate_custom_fields(profile.get('custom_fields', {})).items()
    }
    keys = [field_key(t) or (custom.get(normalize_title(t)) or (None, None))[0] for t in titles]
    filled, pending, locked = [], [], False
    for i, title in enumerate(titles):
        card, key = cards.nth(i), keys[i]
        if not title:
            pending.append("未识别题目")
            continue
        if not key or keys.count(key) != 1:
            pending.append(title)
            continue
        inputs = card.locator(
            'textarea, input:not([type]), input[type=text], input[type=email], input[type=tel], input[type=number], input[type=url]'
        )
        if inputs.count() != 1:
            pending.append(title)
            continue
        control = inputs.first
        if not control.is_visible() or not control.is_enabled() or not control.is_editable():
            locked = True
            continue
        if control.input_value().strip():
            continue
        value = (custom[normalize_title(title)][1] if key.startswith('custom:') else profile.get(key, "")).strip()
        if not value or title in touched:
            pending.append(title)
            continue
        control.fill(value, timeout=1500)
        if control.input_value() == value:
            touched.add(title)
            filled.append(title)
        else:
            pending.append(title)
    if locked:
        return "表单有未开放的输入框：请先登录腾讯文档，或检查征集是否结束。"
    detail = "；请自行填写/核对：" + "、".join(dict.fromkeys(pending)) if pending else ""
    return "已检查可识别字段，请核对后自行点击网页提交" + detail + "。"
