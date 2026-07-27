"""CapabilityGuard 单测（维度① 唯一判定服务，D1/D3）。

- resolve_from_policy：纯判定（已预取策略）——三态 + 工具名/scope 聚合取最严。
- decide：现查（monkeypatch get_agent_scopes_cached 源模块，guard 延迟 import 故生效）。
"""

from __future__ import annotations

from typing import Any

import pytest

from backend.app.hasn.service.agent_capability_guard import capability_guard
from backend.common.security.scope_policy import MODE_ALLOW, MODE_ASK, MODE_DENY


def test_resolve_default_allow_when_no_overrides() -> None:
    mode = capability_guard.resolve_from_policy(
        'allow', {}, tool_name='hasn.community.create_post', required_scopes=['community:post']
    )
    assert mode == MODE_ALLOW


def test_resolve_tool_name_override_deny() -> None:
    mode = capability_guard.resolve_from_policy(
        'allow', {'hasn.community.create_post': 'deny'}, tool_name='hasn.community.create_post', required_scopes=[]
    )
    assert mode == MODE_DENY


def test_resolve_scope_override_ask() -> None:
    mode = capability_guard.resolve_from_policy(
        'allow', {'community:read': 'ask'}, tool_name='hasn.community.get_article', required_scopes=['community:read']
    )
    assert mode == MODE_ASK


def test_resolve_aggregates_most_restrictive() -> None:
    # 工具名 ask + 某 scope deny → 取最严 deny。
    mode = capability_guard.resolve_from_policy(
        'allow',
        {'hasn.x.tool': 'ask', 'b:write': 'deny'},
        tool_name='hasn.x.tool',
        required_scopes=['a:read', 'b:write'],
    )
    assert mode == MODE_DENY


def test_resolve_default_deny_when_no_override() -> None:
    mode = capability_guard.resolve_from_policy('deny', {}, tool_name='hasn.x.tool', required_scopes=['a:read'])
    assert mode == MODE_DENY


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ('policy', 'expected'),
    [
        ({'default_mode': 'allow', 'capability_modes': {}}, MODE_ALLOW),
        ({'default_mode': 'allow', 'capability_modes': {'community:post': 'deny'}}, MODE_DENY),
        ({'default_mode': 'allow', 'capability_modes': {'community:post': 'ask'}}, MODE_ASK),
    ],
)
async def test_decide_live_fetches_policy(
    monkeypatch: pytest.MonkeyPatch, policy: dict[str, Any], expected: str
) -> None:
    import backend.common.security.agent_jwt as agent_jwt_module

    async def _fake(_agent_hasn_id: str, _db: Any) -> dict[str, Any]:  # noqa: RUF029
        return policy

    monkeypatch.setattr(agent_jwt_module, 'get_agent_scopes_cached', _fake)

    db: Any = object()
    mode = await capability_guard.decide(
        db,  # db 未被使用（get_agent_scopes_cached 已 mock）
        agent_hasn_id='a_guard_test',
        tool_name='hasn.community.create_post',
        required_scopes=['community:post'],
    )
    assert mode == expected
