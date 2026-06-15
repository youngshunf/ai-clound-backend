"""AI-Native 工具入参绑定接缝（候选①：manifest ↔ handler 入参单一事实源）。

闭合 GAP：`gateway_internal` 工具的 handler 历史上收到的是**裸 dict**，每个 handler 自己
`input_payload.get('limit', 20)` / `int(...)` 手动兜默认值 + 转型，而 manifest 的
`capability.input_schema` 已经声明了同一套默认值 / 类型 / 枚举 / 上下界——两份会漂移的事实源
（且只有 community 在网关 `_COMMUNITY_INPUT_RULES` 里手抄了一份影子规则，growth/knowledge 零校验）。

本模块把这件事收敛到**一处**：网关在 dispatch 前对 `capability.input_schema` 跑一次
`bind_tool_input`——校验 + 强转 + 回填默认，handler 由此拿到规范化的 typed 入参。

设计取向（务必保持向后兼容，生产系统多会话并发）：
- **附加式、不破坏现有调用**：未在 schema `properties` 声明的字段**原样透传**（不剥离、不报错），
  避免 handler 读取未声明字段时被打断；`additionalProperties: False` 暂不强约束。
- **声明字段从严**：缺 `required` / 类型转不动 / 违反 enum / 越界 → 抛 `ToolInputError`，由网关
  落 15020 deny（如实报错，零 fake）。比旧的「裸 dict 进 handler 然后 KeyError 500」更清晰。
- **纯函数 / 确定性**：给定 (schema, raw) 输出确定 → ask_gate 的 args_hash 因此规范化
  （同一逻辑调用，带不带显式默认值都得到同一 hash）。
- **不可变**：构造新 dict，不改入参 `raw`。
"""

from __future__ import annotations

import copy

from typing import Any


class ToolInputError(Exception):
    """入参校验失败（缺必填 / 类型不符 / 越枚举 / 越界）。``field`` 定位字段，``reason`` 为机器可读原因。"""

    def __init__(self, field: str, reason: str) -> None:
        self.field = field
        self.reason = reason
        super().__init__(f'{field}:{reason}')


_TRUE_TOKENS = {'true', '1', 'yes', 'on'}
_FALSE_TOKENS = {'false', '0', 'no', 'off'}


def bind_tool_input(schema: Any, raw: dict[str, Any] | None) -> dict[str, Any]:
    """按 JSON Schema 绑定工具入参：校验 + 强转 + 回填默认，返回新 dict。

    无 schema / 非 object schema → 原样返回（dict 拷贝）。未声明字段原样透传。
    """
    data = dict(raw or {})
    if not isinstance(schema, dict) or schema.get('type') != 'object':
        return data

    properties: dict[str, Any] = schema.get('properties') or {}
    required: set[str] = set(schema.get('required') or [])
    bound: dict[str, Any] = {}

    for name, prop in properties.items():
        present = name in data and data[name] is not None
        if not present:
            if 'default' in prop:
                bound[name] = copy.deepcopy(prop['default'])
            elif name in required:
                raise ToolInputError(name, 'required')
            # 非必填且无默认 → 不写入（保持稀疏，handler 用 .get 仍安全）
            continue
        value = _coerce(name, prop, data[name], required_field=name in required)
        _validate_constraints(name, prop, value)
        bound[name] = value

    # 未在 properties 声明的字段：原样透传（向后兼容，不剥离 / 不报错）。
    bound.update({name: value for name, value in data.items() if name not in properties})
    return bound


# ---- 类型强转：按声明 type 分派到小函数（保持各函数低复杂度） ----


def _coerce(field: str, prop: dict[str, Any], value: Any, *, required_field: bool) -> Any:
    """按声明 type 强转标量；转不动则 ToolInputError。未声明 / 未知 type 原样透传。"""
    declared = prop.get('type')
    if declared == 'string':
        return _coerce_string(field, value, required_field=required_field)
    if declared == 'integer':
        return _coerce_integer(field, value)
    if declared == 'number':
        return _coerce_number(field, value)
    if declared == 'boolean':
        return _coerce_boolean(field, value)
    if declared == 'array':
        return _coerce_array(field, prop, value)
    if declared == 'object':
        return _coerce_object(field, value)
    return value


def _coerce_string(field: str, value: Any, *, required_field: bool) -> str:
    if isinstance(value, bool):  # bool 是 int 子类，标量转字符串时显式拒绝避免 True→'True'
        raise ToolInputError(field, 'type')
    if isinstance(value, str):
        text = value
    elif isinstance(value, (int, float)):
        text = str(value)  # 与 handler 既有 str(input_payload['x']) 行为一致
    else:
        raise ToolInputError(field, 'type')
    if required_field and not text.strip():
        raise ToolInputError(field, 'required')
    return text


def _coerce_integer(field: str, value: Any) -> int:
    if isinstance(value, bool):  # bool 是 int 子类，显式拒绝避免 True→1 的意外
        raise ToolInputError(field, 'type')
    if isinstance(value, int):
        return value
    if isinstance(value, float) and value.is_integer():
        return int(value)
    if isinstance(value, str):
        try:
            return int(value.strip())
        except ValueError as exc:
            raise ToolInputError(field, 'type') from exc
    raise ToolInputError(field, 'type')


def _coerce_number(field: str, value: Any) -> float:
    if isinstance(value, bool):
        raise ToolInputError(field, 'type')
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        try:
            return float(value.strip())
        except ValueError as exc:
            raise ToolInputError(field, 'type') from exc
    raise ToolInputError(field, 'type')


def _coerce_boolean(field: str, value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, int) and value in (0, 1):
        return bool(value)
    if isinstance(value, str):
        token = value.strip().lower()
        if token in _TRUE_TOKENS:
            return True
        if token in _FALSE_TOKENS:
            return False
    raise ToolInputError(field, 'type')


def _coerce_array(field: str, prop: dict[str, Any], value: Any) -> list[Any]:
    if not isinstance(value, list):
        raise ToolInputError(field, 'type')
    item_schema = prop.get('items')
    if isinstance(item_schema, dict) and item_schema.get('type'):
        return [_coerce(field, item_schema, item, required_field=False) for item in value]
    return list(value)


def _coerce_object(field: str, value: Any) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ToolInputError(field, 'type')
    return value


# ---- 约束校验：枚举 / 长度 / 上下界 / 元素数（按值类型分派） ----


def _validate_constraints(field: str, prop: dict[str, Any], value: Any) -> None:
    if 'enum' in prop and value not in prop['enum']:
        raise ToolInputError(field, 'enum')
    if isinstance(value, str):
        _validate_string_bounds(field, prop, value)
    elif isinstance(value, (int, float)) and not isinstance(value, bool):
        _validate_number_bounds(field, prop, value)
    elif isinstance(value, list):
        _validate_array_bounds(field, prop, value)


def _validate_string_bounds(field: str, prop: dict[str, Any], value: str) -> None:
    if 'minLength' in prop and len(value) < prop['minLength']:
        raise ToolInputError(field, 'min_length')
    if 'maxLength' in prop and len(value) > prop['maxLength']:
        raise ToolInputError(field, 'max_length')


def _validate_number_bounds(field: str, prop: dict[str, Any], value: float) -> None:
    if 'minimum' in prop and value < prop['minimum']:
        raise ToolInputError(field, 'minimum')
    if 'maximum' in prop and value > prop['maximum']:
        raise ToolInputError(field, 'maximum')


def _validate_array_bounds(field: str, prop: dict[str, Any], value: list[Any]) -> None:
    if 'minItems' in prop and len(value) < prop['minItems']:
        raise ToolInputError(field, 'min_items')
    if 'maxItems' in prop and len(value) > prop['maxItems']:
        raise ToolInputError(field, 'max_items')
