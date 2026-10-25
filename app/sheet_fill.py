"""Ephemeral sheet plans and safe input validation; no automatic submission."""

import hashlib, json, re
from core import normalize_title, validate_custom_fields
from sheet_rows import assess_candidate_row
from sheet_headers import analyze_headers


def validate_cell_value(value):
    if not isinstance(value, str) or len(value) > 4000 or any(c in value for c in ('\x00', '\r', '\n', '\t')):
        raise ValueError('每格最多4000字，不支持换行、制表符或空字符。')
    if value.lstrip().startswith(('=', '+', '-', '@')):
        raise ValueError('内容可能被表格解释为公式，请在网页手动填写。')
    if re.fullmatch('[0-9]+', value) and (len(value) > 15 or len(value) > 1 and value.startswith('0')):
        raise ValueError('长数字或前导零可能丢失格式，请先在网页设置文本格式并手动填写。')
    return value


def make_sheet_plan(data, profile, header_row):
    bounds = data['bounds']
    matrix = data['matrix']
    candidate = assess_candidate_row(bounds, matrix, profile, header_row)
    if candidate['candidate_row'] is None:
        raise ValueError(candidate['message'])
    headers = analyze_headers(bounds, matrix, profile)['rows'][header_row - bounds.first_row]['columns']
    custom = {normalize_title(k): v for k, v in validate_custom_fields(profile.get('custom_fields', {})).items()}
    cells = []
    from sheet_headers import column_name

    for h in headers:
        key = h['key']
        value = custom.get(key[7:], '') if key and key.startswith('custom:') else profile.get(key, '') if key else ''
        cells.append(
            {
                'address': f"{column_name(h['column'])}{candidate['candidate_row']}",
                'title': h['title'],
                'key': key,
                'value': value,
                'allowed': bool(key),
            }
        )
    basis = {
        'range': bounds.address,
        'matrix': matrix,
        'header_row': header_row,
        'target_row': candidate['candidate_row'],
        'page_bound': data.get('page_bound') is True,
        'worksheet': data['worksheet'],
        'document_title': data['document_title'],
        'cells': cells,
    }
    token = hashlib.sha256(json.dumps(basis, ensure_ascii=False, sort_keys=True).encode()).hexdigest()
    return {
        'token': token,
        'header_row': header_row,
        'target_row': candidate['candidate_row'],
        'range': bounds.address,
        'worksheet': data['worksheet'],
        'document_title': data['document_title'],
        'cells': cells,
        'page_bound': data.get('page_bound') is True,
    }


def validate_approval(plan, approved):
    if not isinstance(approved, dict):
        raise ValueError('填写确认数据无效。')
    allowed = {c['address'] for c in plan['cells'] if c['allowed']}
    if not set(approved) <= allowed:
        raise ValueError('包含未匹配或不允许填写的列。')
    values = {address: validate_cell_value(value) for address, value in approved.items() if value != ''}
    if not values:
        raise ValueError('没有非空的待填写内容。')
    return values
