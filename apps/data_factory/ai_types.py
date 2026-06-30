"""AI 数据生成类型系统 — 验证生成结果，不合格时用 DataFactory 工具修正"""

import re
from .tools.test_data_tools import TestDataTools
from .tools.random_tools import RandomTools

FIELD_SCHEMAS = {
    'email': {
        'pattern': r'^[^\s@]+@[^\s@]+\.[^\s@]+$',
        'fixer': lambda **kw: TestDataTools.generate_chinese_email(count=1)['result'],
    },
    'phone': {
        'pattern': r'^1[3-9]\d{9}$',
        'fixer': lambda **kw: TestDataTools.generate_chinese_phone(count=1)['result'],
    },
    'id_card': {
        'pattern': r'^\d{17}[\dXx]$',
        'fixer': lambda **kw: TestDataTools.generate_id_card(count=1)['result'],
    },
    'name': {
        'pattern': r'^[一-龥a-zA-Z\s]{2,20}$',
        'fixer': lambda **kw: TestDataTools.generate_chinese_name(
            gender=kw.get('gender', 'random'), count=1
        )['result'],
    },
    'address': {
        'pattern': r'.{5,100}',
        'fixer': lambda **kw: TestDataTools.generate_chinese_address(count=1)['result'],
    },
    'username': {
        'pattern': r'^[a-zA-Z0-9_]{3,20}$',
        'fixer': lambda **kw: RandomTools.random_string(length=8, char_type='alphanumeric', count=1)['result'],
    },
    'integer': {
        'pattern': r'^-?\d+$',
        'fixer': lambda **kw: str(RandomTools.random_int(
            min_val=kw.get('min', 0), max_val=kw.get('max', 9999), count=1
        )['result']),
    },
    'date': {
        'pattern': r'^\d{4}-\d{2}-\d{2}$',
        'fixer': lambda **kw: RandomTools.random_date(
            start_date=kw.get('start', '2024-01-01'),
            end_date=kw.get('end', '2026-12-31'),
            count=1, date_format='%Y-%m-%d'
        )['result'],
    },
    'company': {
        'pattern': r'^[一-龥\w]{2,50}$',
        'fixer': lambda **kw: TestDataTools.generate_company_name(count=1)['result'],
    },
    'bank_card': {
        'pattern': r'^\d{16,19}$',
        'fixer': lambda **kw: TestDataTools.generate_bank_card(count=1)['result'],
    },
    'url': {
        'pattern': r'^https?://[^\s/$.?#].[^\s]*$',
        'fixer': lambda **kw: f"https://example.com/{RandomTools.random_string(length=6, char_type='letters', count=1)['result']}",
    },
}


def _get_schema(type_name: str) -> dict | None:
    """获取字段类型对应的 schema"""
    return FIELD_SCHEMAS.get(type_name)


def validate_field(type_name: str, value) -> bool:
    """验证单个字段值是否符合格式要求"""
    if not isinstance(value, str):
        return True  # 非字符串类型跳过（如数字、布尔）
    schema = _get_schema(type_name)
    if not schema:
        return True  # 无 schema 的字段跳过（视为自由文本）
    return bool(re.match(schema['pattern'], value))


def fix_field(type_name: str, value, field_def: dict = None) -> str:
    """修正无效字段值，回退到 DataFactory 确定性工具"""
    schema = _get_schema(type_name)
    if not schema:
        return value
    try:
        return schema['fixer'](**(field_def or {}))
    except Exception:
        return value


def validate_and_fix_record(record: dict, field_defs: list) -> dict:
    """验证整条记录，对每个无效字段执行修正"""
    fixed = {}
    for field_def in field_defs:
        name = field_def['name']
        type_name = field_def.get('type', 'string')
        raw_value = record.get(name, '')

        if not validate_field(type_name, raw_value):
            fixed[name] = fix_field(type_name, raw_value, field_def)
        else:
            fixed[name] = raw_value
    return fixed
