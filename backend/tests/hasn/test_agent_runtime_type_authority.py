"""分身运行时大脑（runtime_type）云端权威。

修的缺陷（实测）：主人在创建向导里选了 Codex，创建完却自动变回唤星 Runtime。根因是
`_merge_agent_create_payload` 算出的 `runtime_type` **从没传给 `register_hasn_agent`**、
`hasn_agents` 也没有这一列，于是「主人选了什么大脑」在云端没有任何权威记录；节点侧的
自动绑定无从判断，只能一律建 hermes 绑定，把向导刚建好的 codex 绑定顶掉。

本文件钉住三件事：
1. 创建路径把 runtime_type 一路带到 `register_hasn_agent`（此前断在中间）；
2. 读侧 NULL 原样透出——存量行确实没选过，补 'hermes' 是把「不知道」写成「知道」；
3. 换大脑（主人显式激活某条绑定）能改写权威，而节点自动绑定的激活（不带该字段）改不动。
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest

from backend.app.hasn.schema.hasn_agents import CloudCreateAgentRequest, UpdateAgentBindingRequest
from backend.app.hasn.service import hasn_auth as hasn_auth_module
from backend.app.hasn.service.hasn_agents_service import (
    SqlAlchemyAgentProfileGateway,
    _agent_snapshot,
    _merge_agent_create_payload,
)
from backend.common.exception import errors


class _FakeDBImpl:
    """最小 db：只有 create_agent 末尾的 flush 会被调用（查重均已 monkeypatch）。"""

    async def flush(self) -> None:
        return None


_FakeDB: Any = _FakeDBImpl


def _patch_uniqueness(monkeypatch) -> None:
    async def _fake_taken(_db: Any, display_name: str) -> bool:
        return False

    async def _fake_slug(self: Any, _db: Any, *, owner_id: str, base_slug: str) -> str:
        return base_slug

    monkeypatch.setattr(SqlAlchemyAgentProfileGateway, 'is_display_name_taken', staticmethod(_fake_taken))
    monkeypatch.setattr(SqlAlchemyAgentProfileGateway, '_ensure_unique_agent_name', _fake_slug)


def _capture_register(monkeypatch) -> dict[str, Any]:
    captured: dict[str, Any] = {}

    async def _fake_register(**kwargs: Any) -> dict[str, Any]:
        captured.update(kwargs)
        return {
            'agent': SimpleNamespace(hasn_id='a_test', profile_revision=1),
            'agent_key': 'k',
            'already_exists': False,
        }

    monkeypatch.setattr(hasn_auth_module, 'register_hasn_agent', _fake_register, raising=True)
    return captured


# ── 1. 创建路径：主人的选择必须一路带到落库函数 ──
def test_merge_payload_carries_explicit_runtime_type() -> None:
    """请求显式选 codex → payload 带 codex（不被模板默认或 hermes 覆盖）。"""
    request = CloudCreateAgentRequest(
        owner_id='h_owner_1',
        display_name='启航',
        runtime_type='codex',
    )
    payload = _merge_agent_create_payload(request, template=SimpleNamespace(default_runtime_type='hermes'))
    assert payload['runtime_type'] == 'codex'


def test_merge_payload_falls_back_to_template_then_hermes() -> None:
    """未选 → 模板默认；模板也没有 → hermes（向导默认就选中唤星 Runtime，回落是对的）。"""
    request = CloudCreateAgentRequest(owner_id='h_owner_1', display_name='安然')
    assert (
        _merge_agent_create_payload(request, template=SimpleNamespace(default_runtime_type='claude_code'))[
            'runtime_type'
        ]
        == 'claude_code'
    )
    assert _merge_agent_create_payload(request, template=None)['runtime_type'] == 'hermes'


def test_merge_payload_rejects_unknown_runtime_type() -> None:
    """闭集外的值直接拒绝：收进去等于让分身永远绑不上，而云端还显示「已选」。"""
    request = CloudCreateAgentRequest(owner_id='h_owner_1', display_name='启航', runtime_type='gpt5_cli')
    with pytest.raises(errors.RequestError):
        _merge_agent_create_payload(request, template=None)


@pytest.mark.asyncio
async def test_create_agent_passes_runtime_type_to_register(monkeypatch) -> None:
    """回归本次缺陷：payload 里的 runtime_type 必须真的传给 register_hasn_agent。

    修复前这里恒为「未传」——键在 payload 里算好了，然后被丢掉。
    """
    _patch_uniqueness(monkeypatch)
    captured = _capture_register(monkeypatch)

    gateway = SqlAlchemyAgentProfileGateway()
    await gateway.create_agent(
        db=_FakeDB(),
        payload={
            'owner_id': 'h_owner_1',
            'agent_name': 'startup-advisor',
            'display_name': '启航',
            'runtime_type': 'codex',
        },
    )
    assert captured['runtime_type'] == 'codex'


# ── 2. 读侧：NULL 原样透出 ──
def test_snapshot_keeps_null_runtime_type_unfabricated() -> None:
    """存量行 runtime_type 为 NULL → 快照原样 None，不得补 'hermes'。

    补了就等于告诉节点「主人选了 hermes」，节点再也分不出「没选过」和「选了 hermes」，
    而这两者对自动绑定是不同的处置。
    """
    legacy = SimpleNamespace(
        hasn_id='a_legacy', star_id='100003#legacy', owner_id='h_owner_1',
        agent_name='legacy', display_name='存量分身', runtime_type=None,
    )
    assert _agent_snapshot(legacy).runtime_type is None

    typed = SimpleNamespace(
        hasn_id='a_typed', star_id='100003#typed', owner_id='h_owner_1',
        agent_name='typed', display_name='新分身', runtime_type='codex',
    )
    assert _agent_snapshot(typed).runtime_type == 'codex'


# ── 3. 换大脑：只有主人显式激活才改权威 ──
def test_update_binding_request_defaults_runtime_type_to_none() -> None:
    """节点自动绑定回报 binding 状态时不带 runtime_type → 缺省 None = 不动权威。

    这条是防「自动绑定把自己猜出来的类型写成主人的选择」——那样权威当场失真。
    """
    auto = UpdateAgentBindingRequest(binding_node_id='n_local', binding_status='bound')
    assert auto.runtime_type is None

    owner_driven = UpdateAgentBindingRequest(
        binding_node_id='n_local', binding_status='bound', runtime_type='claude_code'
    )
    assert owner_driven.runtime_type == 'claude_code'
