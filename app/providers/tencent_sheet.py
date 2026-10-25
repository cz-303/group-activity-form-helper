"""Experimental, bounded UI-based reads. No private APIs or cell writes."""

import time
from app_core.models import LinkCandidate
from providers.document_links import DocumentRecognizer
from sheet_headers import parse_range, single_address, column_name, MAX_CELL_TEXT
from diagnostics import UserFacingError


def is_tencent_sheet(url):
    info = DocumentRecognizer().recognize(LinkCandidate(url))
    return bool(info and info.provider_id == 'tencent_docs' and info.kind == 'spreadsheet')


def _sheet_name(page):
    tabs = page.get_by_role('tablist', name='工作表', exact=True).get_by_role('tab', selected=True)
    if tabs.count() != 1:
        raise UserFacingError('无法确认唯一的当前工作表，请在网页选中一个工作表后重试。')
    name = tabs.inner_text(timeout=1000).strip()
    if not name or len(name) > 200:
        raise UserFacingError('工作表名称暂时无法读取。')
    return name


def _address_box(page):
    matches = []
    for box in page.get_by_role('textbox').all():
        # Only a real input with a single-cell address is eligible for navigation.
        if box.count() != 1 or not box.is_visible() or not box.is_editable():
            continue
        if box.evaluate('(e)=>e.tagName') != 'INPUT':
            continue
        if single_address(box.input_value(timeout=1000)):
            matches.append(box)
    if len(matches) != 1:
        raise UserFacingError('无法唯一确认单元格地址框；请选中单个单元格并退出编辑模式后重试。')
    return matches[0]


def editing_active(page):
    # A clipped grid editor may retain focus after a normal selection. This
    # zero-size stage was observed on the real page; unknown editors stay blocked.
    return page.evaluate("""() => {
        const active=document.activeElement;
        if(!active?.closest('[role=combobox], [contenteditable=true]')) return false;
        const rich=active.closest('#alloy-rich-text-editor');
        const stage=rich?.closest('.table-input-stage');
        if(stage){const s=getComputedStyle(stage);
            if(s.maxWidth==='0px' && s.maxHeight==='0px' && s.overflow==='hidden') return false;}
        return true;
    }""")


def read_header_region(page, url, range_text, cancelled=lambda: False, timeout=60):
    bounds = parse_range(range_text)
    if not is_tencent_sheet(url) or not is_tencent_sheet(page.url):
        raise UserFacingError('当前页面不是可识别的腾讯在线表格。')
    expected = DocumentRecognizer().recognize(LinkCandidate(url))
    actual = DocumentRecognizer().recognize(LinkCandidate(page.url))
    from urllib.parse import urlsplit, parse_qs

    if urlsplit(expected.normalized_url).path != urlsplit(actual.normalized_url).path:
        raise UserFacingError('页面已打开另一份文档，请返回所选任务对应的表格。')
    expected_tab = parse_qs(urlsplit(url).query).get('tab')
    if expected_tab and expected_tab != parse_qs(urlsplit(page.url).query).get('tab'):
        raise UserFacingError('当前工作表与任务链接指定的工作表不同，请返回后重试。')
    if editing_active(page):
        raise UserFacingError('请先退出单元格或公式栏编辑，并点击单元格地址框，再读取；也可复制表头范围后粘贴分析。')
    guard = (page.url, _sheet_name(page))
    box = _address_box(page)
    original = single_address(box.input_value(timeout=1000))
    editor = page.locator('#alloy-simple-text-editor')
    if editor.count() != 1 or not editor.is_visible():
        raise UserFacingError('当前腾讯表格页面尚未适配公式栏读取；可复制表头范围后粘贴分析。')
    # This is the formula-bar combobox observed on the live page, not the grid editor.
    if editor.get_attribute('role') != 'combobox':
        raise UserFacingError('公式栏结构变化，已停止读取。')
    deadline = time.monotonic() + timeout
    matrix = []
    last_requested = None

    def check():
        if cancelled():
            raise UserFacingError('读取已取消。')
        if time.monotonic() >= deadline:
            raise UserFacingError('表头读取超时，请缩小范围后重试。')
        if (page.url, _sheet_name(page)) != guard:
            raise UserFacingError('文档或工作表已切换，本次读取结果已丢弃。')
        if last_requested and single_address(box.input_value(timeout=1000)) != last_requested:
            raise UserFacingError('单元格选择已改变，本次读取已停止。')
        if editing_active(page):
            raise UserFacingError('检测到编辑区域获得焦点，已停止读取；请使用复制范围后粘贴分析。')

    try:
        for row in range(bounds.first_row, bounds.last_row + 1):
            values = []
            for col in range(bounds.first_column, bounds.last_column + 1):
                check()
                address = f'{column_name(col)}{row}'
                if single_address(box.input_value(timeout=1000)) != (last_requested or original):
                    raise UserFacingError('单元格选择已改变，本次读取已停止。')
                box.fill(address, timeout=1000)
                box.press('Enter', timeout=1000)
                last_requested = address
                page.wait_for_timeout(50)
                check()
                if single_address(box.input_value(timeout=1000)) != address:
                    raise UserFacingError('单元格定位未确认，本次结果已丢弃。')
                first = editor.inner_text(timeout=1000)
                page.wait_for_timeout(30)
                check()
                second = editor.inner_text(timeout=1000)
                if first != second:
                    raise UserFacingError('单元格内容正在变化，请稍候重新读取。')
                if len(second) > MAX_CELL_TEXT:
                    raise UserFacingError('读取区域存在过长内容，请缩小范围。')
                if second in ('\n', '\r\n') and editor.text_content(timeout=1000) == '':
                    second = ''
                values.append(second)
            matrix.append(values)
        check()
        return {
            'matrix': matrix,
            'bounds': bounds,
            'document_title': page.title()[:200],
            'worksheet': guard[1],
            'source': '腾讯网页公式栏（实验性）',
            'page_bound': True,
        }
    finally:
        # Do not fight a user who changed sheets, selection, or started editing.
        try:
            if (
                not cancelled()
                and last_requested
                and (page.url, _sheet_name(page)) == guard
                and single_address(box.input_value(timeout=500)) == last_requested
                and not editing_active(page)
            ):
                box.fill(original, timeout=500)
                box.press('Enter', timeout=500)
        except Exception:
            pass
