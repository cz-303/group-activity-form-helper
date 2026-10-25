"""Explicit approved sheet writes; partial failures never trigger automatic retry."""

import time
from diagnostics import UserFacingError
from providers.tencent_sheet import read_header_region, _sheet_name, _address_box, editing_active
from sheet_headers import parse_range, single_address
from sheet_fill import make_sheet_plan, validate_approval


def apply_sheet_plan(page, url, profile, plan, approved, confirmed_layout=False, cancelled=lambda: False):
    if plan.get('page_bound') is not True:
        raise ValueError('粘贴来源尚未与真实工作表绑定，请先使用网页读取并重新预览。')
    if confirmed_layout is not True:
        raise ValueError('请先核对表格范围、隐藏/筛选、合并及保护状态。')
    values = validate_approval(plan, approved)
    overall_deadline = time.monotonic() + 120
    fresh = read_header_region(page, url, plan['range'], cancelled=cancelled, timeout=60)
    if make_sheet_plan(fresh, profile, plan['header_row'])['token'] != plan['token']:
        raise UserFacingError('表头、数据、工作表或个人资料已变化，请重新读取并预览。')
    guard = (page.url, _sheet_name(page))
    deadline = min(overall_deadline, time.monotonic() + 60)
    written = []

    def check():
        if cancelled() or time.monotonic() >= deadline:
            raise UserFacingError('填写已停止，部分内容可能已保存，请在网页核对。')
        if (page.url, _sheet_name(page)) != guard:
            raise UserFacingError('文档或工作表已切换，已停止后续填写。')

    for cell in plan['cells']:
        address = cell['address']
        if address not in values:
            continue
        check()
        current = read_header_region(
            page, url, f'{address}:{address}', cancelled, timeout=max(0.01, deadline - time.monotonic())
        )
        if current['matrix'] != [['']]:
            raise UserFacingError('目标单元格已有内容，已停止后续填写，不覆盖。')
        if editing_active(page):
            raise UserFacingError('检测到正在编辑，已停止填写，请在网页核对。')
        box = _address_box(page)
        editor = page.locator('#alloy-simple-text-editor')
        box.fill(address, timeout=1000)
        box.press('Enter', timeout=1000)
        check()
        if single_address(box.input_value(timeout=1000)) != address:
            raise UserFacingError('目标位置无法确认，已停止填写。')
        # Check the selected cell immediately before entering any value.
        raw = editor.inner_text(timeout=1000)
        if raw != '' and not (raw in ('\n', '\r\n') and editor.text_content(timeout=1000) == ''):
            raise UserFacingError('目标单元格已变化，不覆盖。')
        editor.fill(values[address], timeout=1500)
        check()
        if single_address(box.input_value(timeout=1000)) != address:
            raise UserFacingError('填写位置已变化，已停止后续操作，请在网页核对。')
        editor.press('Enter', timeout=1500)
        check()
        readback = read_header_region(
            page, url, f'{address}:{address}', cancelled, timeout=max(0.01, deadline - time.monotonic())
        )
        if readback['matrix'] != [[values[address]]]:
            raise UserFacingError('写入后的内容未能准确核验，请在网页检查；不会自动重复写入。')
        written.append(address)
    return {'written': written, 'target_row': plan['target_row']}
