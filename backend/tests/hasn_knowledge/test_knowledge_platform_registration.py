"""knowledge AI-Native 平台接入 注册表一致性测试（纯 Python，零 mock / 零 DB）。

守住 manifest（声明）↔ gateway _internal_handlers()（执行）↔ tool_handlers（实现）三处零漂移：
- 每个 manifest tool.handler 都能在 gateway 注册表解析（缺失会让运行时抛 15050 internal_handler_missing）；
- 2.1.0 新补的 kb 生命周期 + 文档 列/删 四工具（create_kb/delete_kb/list_documents/delete_document）
  在册、scope/risk 正确——这是「分身能完整操作知识库应用」（建库/删库/列文档/删文档）的硬保证。

事实源：docs/HASN-centralized/...（知识库 AI-Native 应用重设计）§2.4；
        backend/app/mcp/apps/knowledge/工具说明.md（与 manifest 由 test_app_tools_doc_consistency 守一致）。
"""

from __future__ import annotations

from backend.app.hasn.service.ai_native_runtime_gateway import ai_native_runtime_gateway
from backend.app.hasn_knowledge.manifest import KNOWLEDGE_AI_NATIVE_MANIFEST
from backend.app.hasn_knowledge.service import tool_handlers

# 2.1.0 新补的四工具（短名）：补齐 kb 生命周期 + 文档 列/删。
_NEW_TOOLS = {
    'create_kb': ('knowledge:write', 'medium'),
    'delete_kb': ('knowledge:write', 'high'),
    'list_documents': ('knowledge:read', 'low'),
    'delete_document': ('knowledge:write', 'medium'),
}


def test_every_knowledge_tool_handler_resolves_in_gateway() -> None:
    """**关键跨切**：每个 manifest tool.handler 都能在 gateway _internal_handlers() 注册表解析。

    handler 缺失会让运行时抛 15050 internal_handler_missing。注册表值即 tool_handlers 模块里
    对应的 handle_knowledge_<flat_name>。
    """
    handlers = ai_native_runtime_gateway._internal_handlers()
    for tool in KNOWLEDGE_AI_NATIVE_MANIFEST['tools']:
        key = tool['handler']
        assert key in handlers, f'gateway 注册表缺 handler: {key}'
        flat = key.split('.', 1)[1]
        assert handlers[key] is getattr(tool_handlers, f'handle_knowledge_{flat}'), key


def test_new_kb_doc_tools_present_with_correct_scope_and_risk() -> None:
    """2.1.0 四个新工具进 tools[] + capabilities[]，scope/risk 与设计一致，mcp_name 全 hasn.knowledge.*。"""
    tools_by_short = {t['tool_id'].split('.', 1)[1]: t for t in KNOWLEDGE_AI_NATIVE_MANIFEST['tools']}
    caps_by_short = {c['tool_id'].split('.', 1)[1]: c for c in KNOWLEDGE_AI_NATIVE_MANIFEST['capabilities']}
    for short, (scope, risk) in _NEW_TOOLS.items():
        assert short in tools_by_short, f'tools[] 缺 {short}'
        assert short in caps_by_short, f'capabilities[] 缺 {short}'
        tool = tools_by_short[short]
        assert tool['transport'] == 'gateway_internal'
        assert tool['required_scopes'] == [scope], short
        assert tool['risk_level'] == risk, short
        cap = caps_by_short[short]
        assert cap['mcp_name'] == f'hasn.knowledge.{short}'
        assert cap['required_scopes'] == [scope], short


def test_kb_write_tools_reuse_existing_scopes() -> None:
    """新工具只复用既有 knowledge:read/write（不新开 scope）→ 已授分身零再授即可用。"""
    used = {s for t in KNOWLEDGE_AI_NATIVE_MANIFEST['tools'] for s in t['required_scopes']}
    assert used <= {'knowledge:read', 'knowledge:write', 'knowledge:upload'}
