"""Read-only status summaries; never stores the user's actual answers."""


def inspect_fields(page):
    cards = page.locator('.question-main-content')
    report = {'missing': [], 'review': [], 'blocked': [], 'filled': 0, 'questions': cards.count()}
    if not report['questions']:
        report['blocked'].append('尚未识别题目，请检查登录状态或页面是否已关闭征集')
        return report
    for i in range(cards.count()):
        card = cards.nth(i)
        title_node = card.locator('.question-title')
        title = title_node.inner_text().strip() if title_node.count() == 1 else f'第{i + 1}题（题名未识别）'
        fields = card.locator(
            'textarea,input:not([type=hidden]):not([type=checkbox]):not([type=radio]):not([type=file]):not([type=button]):not([type=submit])'
        )
        if fields.count() == 1:
            field = fields.first
            if not field.is_visible():
                report['review'].append(title + '：输入区域不可见')
                continue
            if not field.is_enabled() or not field.is_editable():
                report['blocked'].append(title + '：当前不可填写（可能需登录或已截止）')
                continue
            if field.input_value().strip():
                report['filled'] += 1
            else:
                report['missing'].append(title)
        elif card.locator('input[type=file]').count():
            report['review'].append(title + '：请手动上传或核对附件')
        elif card.locator('input[type=radio],input[type=checkbox],[role=combobox],select').count():
            report['review'].append(title + '：选择题，请在网页核对选项')
        else:
            report['review'].append(title + '：题型未适配，请在网页核对')
    return report


def report_summary(report):
    if not report:
        return '尚未检查'
    if report.get('blocked'):
        return '需登录或检查页面'
    missing = len(report.get('missing', []))
    review = len(report.get('review', []))
    if missing or review:
        return f'待补填 {missing} 项 / 核对 {review} 项'
    return '已识别字段无空白·请核对'
