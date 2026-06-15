"""hasn_core 身份 façade 单测（架构候选③ P1，零 mock 业务数据）。

接缝即测试面：验证 (1) 方案 A 的模型/DAO 经 hasn_core 公开即原对象（re-export 契约），
(2) HumanRef/AgentRef DTO 字段映射正确（含 owner_id→owner_hasn_id 重命名），
(3) façade 点查薄委派现有 DAO（委派 + 透传，含 None 透传）。façde 是纯委派接缝，
用 stub 替换其唯一依赖（DAO）测委派本身，不伪造任何业务数据。
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest

from backend.app import hasn_core
from backend.app.hasn.crud.crud_hasn_agents import hasn_agents_dao as _src_agents_dao
from backend.app.hasn.crud.crud_hasn_humans import hasn_humans_dao as _src_humans_dao
from backend.app.hasn.model.hasn_agents import HasnAgents as _SrcAgents
from backend.app.hasn.model.hasn_humans import HasnHumans as _SrcHumans
from backend.app.hasn_core.identity import AgentRef, HumanRef, identity

# ---- (1) re-export 契约：方案 A 公开的就是 app/hasn 原对象 ----


def test_models_reexported_are_same_objects() -> None:
    assert hasn_core.HasnHumans is _SrcHumans
    assert hasn_core.HasnAgents is _SrcAgents


def test_daos_reexported_are_same_singletons() -> None:
    assert hasn_core.hasn_humans_dao is _src_humans_dao
    assert hasn_core.hasn_agents_dao is _src_agents_dao


# ---- (2) DTO 字段映射 ----


def test_human_ref_from_model_maps_fields() -> None:
    m = SimpleNamespace(
        hasn_id='h_1',
        star_id='100001',
        user_id=42,
        nickname='福仔',
        avatar='https://cdn/a.png',
        bio='hi',
        status='active',
        contact_policy={},
    )
    ref = HumanRef.from_model(m)
    assert ref == HumanRef(
        hasn_id='h_1',
        star_id='100001',
        user_id=42,
        nickname='福仔',
        avatar='https://cdn/a.png',
        bio='hi',
        status='active',
    )


def test_agent_ref_from_model_renames_owner_id() -> None:
    m = SimpleNamespace(
        hasn_id='a_1',
        star_id='100002#star',
        owner_id='h_1',
        display_name='星诺',
        agent_name='all_rounder',
        avatar=None,
        profession='全能助理',
        bio='b',
    )
    ref = AgentRef.from_model(m)
    assert ref.owner_hasn_id == 'h_1'  # 模型 owner_id → DTO owner_hasn_id
    assert ref.hasn_id == 'a_1'
    assert ref.profession == '全能助理'


# ---- (3) façade 点查薄委派 DAO ----
# 直接在共享 DAO 单例上替换方法（façade 与本测试引用的是同一 hasn_humans_dao 对象），
# 测委派 + 透传，不伪造任何业务数据。


def _recording(ret: Any, log: list) -> Any:
    async def _fn(db: Any, key: Any) -> Any:  # noqa: RUF029 — 替身需匹配被替换的 async DAO 方法签名
        log.append(key)
        return ret

    return _fn


@pytest.mark.asyncio
async def test_get_human_by_user_id_delegates(monkeypatch: pytest.MonkeyPatch) -> None:
    sentinel = SimpleNamespace(hasn_id='h_9', user_id=7)
    log: list = []
    monkeypatch.setattr(_src_humans_dao, 'get_by_user_id', _recording(sentinel, log))
    out = await identity.get_human_by_user_id(db=None, user_id=7)
    assert out is sentinel  # 透传 DAO 返回
    assert log == [7]


@pytest.mark.asyncio
async def test_ref_human_returns_none_when_absent(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(_src_humans_dao, 'get_by_hasn_id', _recording(None, []))
    assert await identity.ref_human(db=None, hasn_id='missing') is None


@pytest.mark.asyncio
async def test_ref_human_maps_dto_when_present(monkeypatch: pytest.MonkeyPatch) -> None:
    model = SimpleNamespace(
        hasn_id='h_2',
        star_id='100003',
        user_id=8,
        nickname='n',
        avatar=None,
        bio=None,
        status='active',
    )
    monkeypatch.setattr(_src_humans_dao, 'get_by_hasn_id', _recording(model, []))
    ref = await identity.ref_human(db=None, hasn_id='h_2')
    assert isinstance(ref, HumanRef)
    assert ref.hasn_id == 'h_2' and ref.user_id == 8
