"""`bind_tool_input` 纯函数单测（零 mock）：校验 + 强转 + 回填默认 + 向后兼容透传。

接缝即测试面（候选①）：manifest input_schema 是工具入参的唯一事实源；这里穷举绑定器的
默认值回填、类型强转、约束校验、未声明字段透传等行为，handler 由此可信地拿到规范化入参。
"""

from __future__ import annotations

import pytest

from backend.app.mcp.tools.input_binding import ToolInputError, bind_tool_input

# 取自 hasn_growth.manifest collect_start 的真实 schema 形状（含 default / min / max / required）。
_COLLECT_SCHEMA = {
    'type': 'object',
    'properties': {
        'keyword': {'type': 'string', 'minLength': 1, 'maxLength': 200},
        'source_types': {'type': 'array', 'items': {'type': 'string'}},
        'max_pages': {'type': 'integer', 'minimum': 1, 'maximum': 100, 'default': 5},
        'max_results': {'type': 'integer', 'minimum': 1, 'maximum': 10000, 'default': 100},
        'request_config': {'type': 'object'},
    },
    'required': ['keyword'],
    'additionalProperties': False,
}


def test_fills_defaults_for_missing_optional_fields() -> None:
    bound = bind_tool_input(_COLLECT_SCHEMA, {'keyword': 'ai'})
    assert bound['keyword'] == 'ai'
    assert bound['max_pages'] == 5  # 来自 schema default，handler 无需再兜
    assert bound['max_results'] == 100
    # 无 default 且非必填 → 不写入（保持稀疏）
    assert 'source_types' not in bound
    assert 'request_config' not in bound


def test_missing_required_raises() -> None:
    with pytest.raises(ToolInputError) as exc:
        bind_tool_input(_COLLECT_SCHEMA, {'max_pages': 3})
    assert exc.value.field == 'keyword'
    assert exc.value.reason == 'required'


def test_empty_required_string_is_required_error() -> None:
    with pytest.raises(ToolInputError) as exc:
        bind_tool_input(_COLLECT_SCHEMA, {'keyword': '   '})
    assert exc.value.field == 'keyword'
    assert exc.value.reason == 'required'


def test_explicit_value_overrides_default() -> None:
    bound = bind_tool_input(_COLLECT_SCHEMA, {'keyword': 'ai', 'max_pages': 12})
    assert bound['max_pages'] == 12


def test_numeric_string_coerced_to_integer() -> None:
    bound = bind_tool_input(_COLLECT_SCHEMA, {'keyword': 'ai', 'max_pages': '7'})
    assert bound['max_pages'] == 7
    assert isinstance(bound['max_pages'], int)


def test_integer_out_of_range_raises() -> None:
    with pytest.raises(ToolInputError) as exc:
        bind_tool_input(_COLLECT_SCHEMA, {'keyword': 'ai', 'max_pages': 999})
    assert exc.value.field == 'max_pages'
    assert exc.value.reason == 'maximum'

    with pytest.raises(ToolInputError) as exc2:
        bind_tool_input(_COLLECT_SCHEMA, {'keyword': 'ai', 'max_pages': 0})
    assert exc2.value.reason == 'minimum'


def test_non_numeric_string_for_integer_raises_type() -> None:
    with pytest.raises(ToolInputError) as exc:
        bind_tool_input(_COLLECT_SCHEMA, {'keyword': 'ai', 'max_pages': 'abc'})
    assert exc.value.field == 'max_pages'
    assert exc.value.reason == 'type'


def test_bool_rejected_for_integer() -> None:
    # bool 是 int 子类，显式拒绝避免 True→1 的意外
    with pytest.raises(ToolInputError) as exc:
        bind_tool_input(_COLLECT_SCHEMA, {'keyword': 'ai', 'max_pages': True})
    assert exc.value.reason == 'type'


def test_string_maxlength_enforced() -> None:
    with pytest.raises(ToolInputError) as exc:
        bind_tool_input(_COLLECT_SCHEMA, {'keyword': 'x' * 201})
    assert exc.value.field == 'keyword'
    assert exc.value.reason == 'max_length'


def test_array_items_coerced() -> None:
    schema = {
        'type': 'object',
        'properties': {'ids': {'type': 'array', 'items': {'type': 'integer'}}},
    }
    bound = bind_tool_input(schema, {'ids': ['1', 2, '3']})
    assert bound['ids'] == [1, 2, 3]


def test_array_type_mismatch_raises() -> None:
    schema = {'type': 'object', 'properties': {'ids': {'type': 'array'}}}
    with pytest.raises(ToolInputError) as exc:
        bind_tool_input(schema, {'ids': 'not-a-list'})
    assert exc.value.reason == 'type'


def test_enum_violation_raises() -> None:
    schema = {
        'type': 'object',
        'properties': {'kind': {'type': 'string', 'enum': ['post', 'article']}},
        'required': ['kind'],
    }
    with pytest.raises(ToolInputError) as exc:
        bind_tool_input(schema, {'kind': 'comment'})
    assert exc.value.field == 'kind'
    assert exc.value.reason == 'enum'
    # 合法枚举透过
    assert bind_tool_input(schema, {'kind': 'post'})['kind'] == 'post'


def test_boolean_coercion_from_string_tokens() -> None:
    schema = {'type': 'object', 'properties': {'flag': {'type': 'boolean'}}}
    assert bind_tool_input(schema, {'flag': 'true'})['flag'] is True
    assert bind_tool_input(schema, {'flag': 'off'})['flag'] is False
    assert bind_tool_input(schema, {'flag': 1})['flag'] is True


def test_string_field_coerces_scalar_to_str() -> None:
    # 与 handler 既有 `str(input_payload['post_id'])` 行为一致：标量数字转字符串
    schema = {'type': 'object', 'properties': {'post_id': {'type': 'string'}}, 'required': ['post_id']}
    bound = bind_tool_input(schema, {'post_id': 12345})
    assert bound['post_id'] == '12345'


def test_unknown_keys_passed_through_backward_compat() -> None:
    # additionalProperties: False 不强约束——未声明字段原样透传，避免打断现有 handler
    bound = bind_tool_input(_COLLECT_SCHEMA, {'keyword': 'ai', 'legacy_extra': 'keep-me'})
    assert bound['legacy_extra'] == 'keep-me'


def test_no_schema_returns_copy_unchanged() -> None:
    raw = {'a': 1, 'b': 2}
    assert bind_tool_input(None, raw) == raw
    assert bind_tool_input({'type': 'string'}, raw) == raw  # 非 object schema 不处理


def test_does_not_mutate_input() -> None:
    raw = {'keyword': 'ai'}
    bind_tool_input(_COLLECT_SCHEMA, raw)
    assert raw == {'keyword': 'ai'}  # 原 dict 未被写入默认值


def test_none_value_treated_as_absent_and_defaulted() -> None:
    bound = bind_tool_input(_COLLECT_SCHEMA, {'keyword': 'ai', 'max_pages': None})
    assert bound['max_pages'] == 5
