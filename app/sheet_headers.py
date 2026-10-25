"""Bounded sheet coordinates and header analysis; no browser, UI or writes."""

from dataclasses import dataclass
import re
import csv, io
from core import field_key, normalize_title, validate_custom_fields

MAX_ROWS = 20
MAX_COLUMNS = 26
MAX_CELLS = 160
MAX_CELL_TEXT = 4000
SENSITIVE_HEADER = re.compile('密码|验证码|口令|密钥|令牌')


def header_is_unsafe(title):
    return len(title) > 120 or title.startswith(('=', '+', '-', '@')) or bool(SENSITIVE_HEADER.search(title))


def column_name(number):
    if type(number) is not int or not 1 <= number <= 16384:
        raise ValueError('列号超出表格范围。')
    result = ''
    while number:
        number, remainder = divmod(number - 1, 26)
        result = chr(65 + remainder) + result
    return result


def column_number(name):
    if not isinstance(name, str) or not re.fullmatch('[A-Z]{1,3}', name):
        raise ValueError('列名无效。')
    number = 0
    for char in name:
        number = number * 26 + ord(char) - 64
    if not 1 <= number <= 16384:
        raise ValueError('列号超出表格范围。')
    return number


@dataclass(frozen=True)
class ReadRange:
    first_row: int
    last_row: int
    first_column: int
    last_column: int

    @property
    def address(self):
        return f'{column_name(self.first_column)}{self.first_row}:{column_name(self.last_column)}{self.last_row}'


def parse_range(text):
    if not isinstance(text, str) or len(text) > 40:
        raise ValueError('请输入矩形范围，例如 A1:P8。')
    match = re.fullmatch(
        r'\$?([A-Z]{1,3})\$?([1-9][0-9]{0,6}):\$?([A-Z]{1,3})\$?([1-9][0-9]{0,6})', text.strip().upper()
    )
    if not match:
        raise ValueError('请输入矩形范围，例如 A1:P8，不支持整行或整列读取。')
    c1, r1, c2, r2 = match.groups()
    bounds = ReadRange(int(r1), int(r2), column_number(c1), column_number(c2))
    rows = bounds.last_row - bounds.first_row + 1
    cols = bounds.last_column - bounds.first_column + 1
    if (
        not 1 <= rows <= MAX_ROWS
        or not 1 <= cols <= MAX_COLUMNS
        or bounds.last_row > 1048576
        or rows * cols > MAX_CELLS
    ):
        raise ValueError('每次最多20行、26列且合计160个单元格，请缩小范围。')
    return bounds


def single_address(text):
    if not isinstance(text, str):
        return None
    match = re.fullmatch(r'\$?([A-Z]{1,3})\$?([1-9][0-9]{0,6})', text.strip().upper())
    if not match:
        return None
    try:
        col = column_number(match[1])
        row = int(match[2])
        return f'{column_name(col)}{row}' if row <= 1048576 else None
    except ValueError:
        return None


def parse_pasted_region(text, first_cell='A1', require_rectangular=False):
    """Excel/Tencent copied rectangles are TSV; quoted multiline text is retained."""
    address = single_address(first_cell)
    if not address:
        raise ValueError('请输入粘贴区域左上角单元格，例如 A1。')
    if not isinstance(text, str) or not text.strip() or len(text) > 128000 or '\x00' in text:
        raise ValueError('请粘贴最多160个单元格的表头区域文字，不能包含空字符。')
    values = []
    try:
        for row in csv.reader(io.StringIO(text), delimiter='\t', strict=True):
            if len(values) >= MAX_ROWS or len(row) > MAX_COLUMNS or any(len(v) > MAX_CELL_TEXT for v in row):
                raise ValueError('粘贴区域过大，请缩小到20行、26列、160个单元格以内。')
            values.append(row)
    except csv.Error as error:
        raise ValueError('粘贴内容格式不完整，请重新复制矩形单元格区域。') from error
    width = max((len(r) for r in values), default=0)
    if not width:
        raise ValueError('没有可分析的单元格文字。')
    if require_rectangular and any(len(row) != width for row in values):
        raise ValueError('粘贴内容各行列数不一致，不能把缺失内容当成空白；请重新复制矩形区域，包含表头和数据行。')
    start = parse_range(address + ':' + address)
    bounds = parse_range(f'{address}:{column_name(start.first_column + width - 1)}{start.first_row + len(values) - 1}')
    return bounds, [r + [''] * (width - len(r)) for r in values]


def analyze_headers(bounds, matrix, profile):
    """Candidate rows require two distinct known fields; never authorize filling."""
    if not isinstance(bounds, ReadRange) or parse_range(bounds.address) != bounds:
        raise ValueError('读取范围无效。')
    rows = bounds.last_row - bounds.first_row + 1
    cols = bounds.last_column - bounds.first_column + 1
    if (
        not isinstance(matrix, list)
        or len(matrix) != rows
        or any(not isinstance(r, list) or len(r) != cols for r in matrix)
    ):
        raise ValueError('单元格读取结果不完整，请重新读取。')
    if any(not isinstance(v, str) or len(v) > MAX_CELL_TEXT for r in matrix for v in r):
        raise ValueError('单元格内容类型无效或过长。')
    custom_values = {normalize_title(k): v for k, v in validate_custom_fields(profile.get('custom_fields', {})).items()}
    custom = set(custom_values)
    result = []
    candidates = []
    for offset, values in enumerate(matrix):
        row = bounds.first_row + offset
        keys = []
        for value in values:
            title = value.strip()
            key = None if header_is_unsafe(title) else field_key(title)
            if not key and title and not header_is_unsafe(title) and normalize_title(title) in custom:
                key = 'custom:' + normalize_title(title)
            keys.append(key)
        columns = []
        for index, (value, key) in enumerate(zip(values, keys)):
            title = value.strip()
            state = '已匹配资料' if key else '未匹配，可在设置中添加自定义资料'
            if key and not (custom_values.get(key[7:], '') if key.startswith('custom:') else profile.get(key, '')):
                state = '已匹配，资料为空，请在设置补充'
            if not title:
                state = '空白列，需核对是否属于表头'
            elif len(title) > 120 or title.startswith(('=', '+', '-', '@')):
                state = '内容较长或为公式，需人工核对'
            elif SENSITIVE_HEADER.search(title):
                state = '验证或敏感字段，不支持匹配'
            elif key and keys.count(key) > 1:
                state = '匹配字段重复，需人工核对'
            columns.append(
                {
                    'address': f'{column_name(bounds.first_column + index)}{row}',
                    'column': bounds.first_column + index,
                    'title': title,
                    'key': key,
                    'state': state,
                }
            )
        distinct = len({k for k in keys if k})
        nonblank = sum(bool(v.strip()) for v in values)
        candidate = distinct >= 2
        confidence = round(min(0.95, 0.45 + 0.1 * distinct), 2) if candidate else 0.0
        result.append(
            {
                'row': row,
                'columns': columns,
                'matched_fields': distinct,
                'candidate': candidate,
                'confidence': confidence,
                'nonblank': nonblank,
            }
        )
        if candidate:
            candidates.append(row)
    return {
        'range': bounds.address,
        'rows': result,
        'candidates': candidates,
        'suggested_row': candidates[0] if len(candidates) == 1 else None,
        'status': 'empty'
        if not any(r['nonblank'] for r in result)
        else 'success'
        if len(candidates) == 1
        else 'warning',
    }
