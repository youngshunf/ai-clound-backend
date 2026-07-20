"""系统注入保留字段 `_hasn_*` 与工具 schema 严格性的守卫（doc14 §6.2）。

**血泪根因**：MCP SDK 在把请求交给我们的 `call_tool` 之前，就先拿工具声明的 `inputSchema`
校验了**未剥离**的 wire 入参；而 Hermes 逐调用把 `_hasn_session_id` 打进入参，剥离在 SDK 校验
之后。于是所有声明 `additionalProperties: false` 的工具（截至本次 63 处声明，含 13 个
AI-Native 应用 manifest）都会被判
``Additional properties are not allowed ('_hasn_session_id' was unexpected)`` 直接拒掉——
`hasn.cloud.tool.search` 首当其冲：分身连工具都发现不了，连续失败触发 Runtime MCP 熔断，
把整个 cloud server 判为不可达、全线工具雪崩。

本文件钉死两条不变量，防回归：
1. 保留字段在 wire 上放行（否则分身发现不了工具）；
2. **其余未知键照旧严格拒绝**（开口只对保留命名空间，严格度零损失）。
"""

from __future__ import annotations

import jsonschema
import pytest

from backend.app.mcp.trust_gate import (
    RESERVED_ALLOWED_TOOL_NAMES,
    RESERVED_IS_EXTERNAL,
    RESERVED_PEER_ID,
    RESERVED_PEER_TRUST,
    RESERVED_SESSION_ID,
    allow_reserved_fields_in_schema,
)

_STRICT_SCHEMA = {
    'type': 'object',
    'properties': {'query': {'type': 'string'}},
    'required': ['query'],
    'additionalProperties': False,
}


# ── 纯函数契约 ────────────────────────────────────────────────────────────────
def test_strict_schema_gains_reserved_pattern() -> None:
    """严格 schema 补出 `^_hasn_` 放行位，且 properties 不被污染（分身读 schema 看不到保留字段）。"""
    out = allow_reserved_fields_in_schema(_STRICT_SCHEMA)
    assert out['patternProperties'] == {'^_hasn_': {}}
    assert out['properties'] == {'query': {'type': 'string'}}
    assert out['additionalProperties'] is False  # 严格度不降级


def test_does_not_mutate_input_schema() -> None:
    """绝不就地改原 schema——工具 input_schema 常是模块级字面量，改了会污染全局。"""
    original = dict(_STRICT_SCHEMA)
    out = allow_reserved_fields_in_schema(_STRICT_SCHEMA)
    assert _STRICT_SCHEMA == original
    assert 'patternProperties' not in _STRICT_SCHEMA
    assert out is not _STRICT_SCHEMA


def test_open_schema_untouched() -> None:
    """未声明 additionalProperties:false 的 schema 本就放行未知键，原样返回不加噪。"""
    open_schema = {'type': 'object', 'properties': {'query': {'type': 'string'}}}
    assert allow_reserved_fields_in_schema(open_schema) is open_schema


def test_existing_pattern_properties_preserved() -> None:
    """已有 patternProperties 的 schema：合并而非覆盖。"""
    schema = {**_STRICT_SCHEMA, 'patternProperties': {'^x_': {'type': 'string'}}}
    out = allow_reserved_fields_in_schema(schema)
    assert out['patternProperties'] == {'^x_': {'type': 'string'}, '^_hasn_': {}}


def test_idempotent() -> None:
    """反复投影不叠加（list_tools 每请求都走一遍）。"""
    once = allow_reserved_fields_in_schema(_STRICT_SCHEMA)
    assert allow_reserved_fields_in_schema(once) is once


# ── 真 jsonschema 校验（复刻 SDK 那一步）──────────────────────────────────────
@pytest.mark.parametrize(
    'reserved',
    [
        RESERVED_SESSION_ID,
        RESERVED_ALLOWED_TOOL_NAMES,
        RESERVED_IS_EXTERNAL,
        RESERVED_PEER_ID,
        RESERVED_PEER_TRUST,
    ],
)
def test_reserved_fields_pass_sdk_validation(reserved: str) -> None:
    """五个保留字段都能过 SDK 同款校验（这正是原先 tool.search 被拒的那一步）。"""
    schema = allow_reserved_fields_in_schema(_STRICT_SCHEMA)
    jsonschema.validate(instance={'query': 'sources', reserved: 'x'}, schema=schema)


def test_unknown_field_still_rejected() -> None:
    """非保留命名空间的未知键**仍然**被拒——开口只对 `_hasn_*`，不是把门拆了。"""
    schema = allow_reserved_fields_in_schema(_STRICT_SCHEMA)
    with pytest.raises(jsonschema.ValidationError):
        jsonschema.validate(instance={'query': 'sources', 'bogus': 1}, schema=schema)


def test_real_tool_search_schema_accepts_injected_session_id() -> None:
    """真·`hasn.cloud.tool.search` schema（活体现场翻车的那个）投影后接得住注入字段。"""
    from backend.app.mcp.server import mcp_server
    from backend.app.mcp.tool_directory import ToolDirectoryService
    from backend.app.mcp.tools.tool_search import ToolSearchTool

    # 用活体 server 的 registry，不另造一份（免得测的是假目录）。
    tool = ToolSearchTool(ToolDirectoryService(mcp_server.tool_registry))
    assert tool.input_schema['additionalProperties'] is False  # 前提仍成立才有本测试
    schema = allow_reserved_fields_in_schema(tool.input_schema)
    jsonschema.validate(instance={'query': 'sources', RESERVED_SESSION_ID: 'sess_x'}, schema=schema)
