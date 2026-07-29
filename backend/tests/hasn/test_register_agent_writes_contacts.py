"""R3：register_hasn_agent 创建 HasnAgents 后必须同事务写入关系命令 outbox。

测试策略: AsyncMock 替换 AsyncSession，按调用顺序返回 SELECT / INSERT 的 mock 结果，
断言最后一次 execute 为 PG INSERT hasn_relation_command_outbox，并校验关键字段。不依赖真实数据库
(DB 接入在集成测试层覆盖)。
"""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from sqlalchemy.dialects.postgresql import Insert as PGInsert
from sqlalchemy.dialects.postgresql import dialect as pg_dialect

from backend.app.hasn.service import hasn_auth


class _HumanStub:
    def __init__(self, hasn_id: str, star_id: str) -> None:
        self.hasn_id = hasn_id
        self.star_id = star_id
        self.user_id = 1


def _make_scalar_result(value: object | None) -> MagicMock:
    """返回一个满足 result.scalar_one_or_none() 的 mock。"""
    result = MagicMock()
    result.scalar_one_or_none.return_value = value
    return result


@pytest.mark.asyncio
async def test_register_hasn_agent_enqueues_relation_command() -> None:
    """注册新 agent 后应登记一条 owner-agent 控制边命令。"""
    owner = _HumanStub(hasn_id='h_owner_test', star_id='100001')

    db = AsyncMock()
    db.execute.side_effect = [
        _make_scalar_result(owner),  # 查 owner HasnHumans
        _make_scalar_result(None),   # 查是否已存在 agent (幂等)
        _make_scalar_result(None),   # INSERT hasn_relation_command_outbox
    ]
    db.flush = AsyncMock()
    db.add = MagicMock()

    result = await hasn_auth.register_hasn_agent(
        db=db,
        owner_hasn_id=owner.hasn_id,
        agent_name='test_agent',
        display_name='测试 Agent',
    )

    # 基本契约：新 agent 创建并返回明文 agent_key。
    assert result['already_exists'] is False
    assert result['agent'] is not None
    assert result['agent_key'] is not None and result['agent_key'].startswith('hasn_ak_')

    # 第三次 db.execute 应该是关系命令 outbox INSERT。
    assert db.execute.call_count == 3, f'expected 3 execute calls, got {db.execute.call_count}'
    outbox_stmt = db.execute.call_args_list[2].args[0]
    assert isinstance(outbox_stmt, PGInsert), (
        f'expected pg_insert statement, got {type(outbox_stmt).__name__}'
    )
    assert outbox_stmt.table.name == 'hasn_relation_command_outbox'

    # 校验 ON CONFLICT DO NOTHING 绑定到正确的索引字段。
    on_conflict = outbox_stmt._post_values_clause
    assert on_conflict is not None, 'ON CONFLICT clause missing'
    inferred_target_elements = getattr(on_conflict, 'inferred_target_elements', None)
    elements = [
        el if isinstance(el, str) else el.name
        for el in (inferred_target_elements or [])
    ]
    assert set(elements) == {'idempotency_key'}, (
        f'unexpected conflict target: {elements}'
    )

    values = outbox_stmt.compile(dialect=pg_dialect()).params
    assert values['command_type'] == 'ensure_owner_agent_control_edge'
    assert values['owner_hasn_id'] == owner.hasn_id
    assert values['peer_hasn_id'].startswith('a_')
    assert values['idempotency_key'] == (
        f"owner-agent-control:{owner.hasn_id}:{values['peer_hasn_id']}"
    )
    assert values['status'] == 'pending'
    assert result['agent'].hasn_id == values['peer_hasn_id']


@pytest.mark.asyncio
async def test_register_hasn_agent_idempotent_skips_insert_on_existing() -> None:
    """已存在的 agent 幂等返回，不应重复登记关系命令。"""
    owner = _HumanStub(hasn_id='h_owner_idem', star_id='100002')

    class _ExistingAgent:
        hasn_id = 'a_existing'
        star_id = '100002#test_agent'  # 真实 existing agent 必有 star_id（建档即替换渲染 {{star_id}} 需要）
        node_id = 'node_old'
        display_name = '测试 Agent'
        agent_name = 'test_agent'
        type = 'desktop'
        avatar = None
        created_time = None
        soul_md = None
        agents_md = None
        user_md = None
        memory_md = None

    db = AsyncMock()
    db.execute.side_effect = [
        _make_scalar_result(owner),             # 查 owner
        _make_scalar_result(_ExistingAgent()),  # 已有 agent → 幂等返回
    ]
    db.flush = AsyncMock()
    db.add = MagicMock()

    result = await hasn_auth.register_hasn_agent(
        db=db,
        owner_hasn_id=owner.hasn_id,
        agent_name='test_agent',
        display_name='测试 Agent',
    )

    assert result['already_exists'] is True
    assert result['agent_key'] is None
    # 不应有第 3 次 execute（INSERT outbox），避免重复写。
    assert db.execute.call_count == 2, (
        f'expected 2 execute calls for idempotent path, got {db.execute.call_count}'
    )
