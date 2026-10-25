"""Read-only candidate rows inside a bounded snapshot; never a write permission."""

from sheet_headers import analyze_headers, column_name, header_is_unsafe
from core import normalize_title


def assess_candidate_row(bounds, matrix, profile, header_row):
    analysis = analyze_headers(bounds, matrix, profile)
    result = {
        'candidate_row': None,
        'candidate_range': None,
        'can_write': False,
        'status': 'warning',
        'occupied_rows': [],
        'message': '',
    }
    if type(header_row) is not int or not bounds.first_row <= header_row <= bounds.last_row:
        raise ValueError('请先选择读取范围内的表头行。')
    header = analysis['rows'][header_row - bounds.first_row]
    columns = header['columns']
    named = [c for c in columns if c['title']]
    if len(named) < 2:
        result['message'] = '所选行不足两个非空列名，无法检查空行候选。'
        return result
    titles = [normalize_title(c['title']) for c in named]
    keys = [c['key'] for c in named if c['key']]
    if len(set(titles)) != len(titles) or len(set(keys)) != len(keys):
        result['message'] = '表头列名或匹配字段重复，请先核对并缩小读取范围。'
        return result
    first = named[0]['column']
    last = named[-1]['column']
    if any(not c['title'] for c in columns if first <= c['column'] <= last):
        result['message'] = '表头中间存在空白列，可能有合并单元格，请先在网页核对。'
        return result
    if any(header_is_unsafe(c['title']) for c in named):
        result['message'] = '表头含公式、过长内容或验证字段，不能确认空行候选。'
        return result
    if any(row != header_row for row in analysis['candidates']):
        result['message'] = '读取区域含有其他候选表头，可能是分段表格，请缩小为一个表格区域。'
        return result
    if header_row == bounds.last_row:
        result['status'] = 'empty'
        result['message'] = '当前只读到表头，没有数据行；请扩大范围并重新读取。'
        return result
    occupied = []
    for row in range(header_row + 1, bounds.last_row + 1):
        # Even whitespace and formula text are occupied; no trimming or guessing.
        if any(value != '' for value in matrix[row - bounds.first_row]):
            occupied.append(row)
    result['occupied_rows'] = occupied
    candidate = (occupied[-1] if occupied else header_row) + 1
    if candidate > bounds.last_row:
        result['status'] = 'empty'
        result['message'] = '读取范围末行已有内容；请扩大范围并重新读取，不能推断下一行为空。'
        return result
    result.update(
        candidate_row=candidate,
        candidate_range=f'{column_name(bounds.first_column)}{candidate}:{column_name(bounds.last_column)}{candidate}',
        message=f'范围内空行候选：第{candidate}行（{column_name(bounds.first_column)}{candidate}:{column_name(bounds.last_column)}{candidate}）。仅检查快照文字，不代表整行可填写；隐藏、筛选、合并、公式及保护状态尚未验证。',
    )
    return result
