"""平台工具 · memory 域 真实 service 测试（禁 mock，doc16 Phase C1）。

验证记忆工具迁云端权威（hasn.memory.{save,search,recall,list}）：
- 注册齐全（4 个），工具名/命名空间/source/execution_location/scope 与设计 1:1；
- input_schema 关键约束（save 必填 predicate+object；读类无 required）防回归；
- 真实 PG 往返：save agent_self/owner/peer/world → search/recall/list 各路读回；
  owner 隔离硬边界、subject 规约（agent_self/owner 自动推断 subject_id）、world 非
  global 作用域纠偏、confidence 置信度落 [0,1]、只读取 active。

需活体 DB（本地 15432）：
    DATABASE_PORT=15432 pytest backend/tests/mcp/test_memory_tools.py
无 DB 时跳过（不伪造）。
"""

from __future__ import annotations

import uuid

from typing import TYPE_CHECKING

import pytest

from backend.app.mcp.auth import AgentContext
from backend.app.mcp.tools.memory import MEMORY_TOOLS

if TYPE_CHECKING:
    from backend.app.mcp.tools.base import BaseTool


def _tool(name: str) -> BaseTool:
    for t in MEMORY_TOOLS:
        if t.name == name:
            return t
    raise AssertionError(f'memory 工具未注册: {name}')


def _agent_ctx(owner_hasn_id: str, agent_hasn_id: str = 'a_memory_tools_test') -> AgentContext:
    return AgentContext(
        hasn_id=agent_hasn_id,
        owner_id=1,
        agent_status='active',
        metadata={},
        owner_hasn_id=owner_hasn_id,
        session_uuid='amk_memory_tools_test',
    )


async def _db_reachable() -> bool:
    try:
        from sqlalchemy import text

        from backend.database.db import async_db_session

        async with async_db_session() as db:
            await db.execute(text('SELECT 1'))
    except Exception:
        return False
    else:
        return True


# ── 注册/契约（无需 DB）────────────────────────────────────────────────────────
_EXPECTED_NAMES = {
    'hasn.memory.save',
    'hasn.memory.search',
    'hasn.memory.recall',
    'hasn.memory.list',
}


def test_memory_tools_register_exactly_four() -> None:
    """4 个记忆工具全注册，名称与设计 1:1。"""
    names = {t.name for t in MEMORY_TOOLS}
    assert names == _EXPECTED_NAMES, f'差异: {names ^ _EXPECTED_NAMES}'


def test_memory_tools_are_cloud_platform() -> None:
    """全部 source=platform、namespace=hasn.memory、execution_location=cloud。"""
    for t in MEMORY_TOOLS:
        assert t.source == 'platform'
        assert t.namespace == 'hasn.memory'
        assert t.execution_location == 'cloud'


def test_memory_scope_split_read_vs_write() -> None:
    """读类（search/recall/list）无 scope；写类（save）声明 memory:write（出厂 Allow）。"""
    for t in MEMORY_TOOLS:
        if t.name == 'hasn.memory.save':
            assert t.required_scopes == ['memory:write'], 'save 写类应声明 memory:write'
        else:
            assert t.required_scopes == [], f'{t.name} 读类不应有 scope'


def test_save_required_fields_and_reads_have_no_required() -> None:
    """save 必填 predicate+object；读类不应有 required。"""
    assert _tool('hasn.memory.save').input_schema['required'] == ['predicate', 'object']
    assert 'required' not in _tool('hasn.memory.recall').input_schema
    assert 'required' not in _tool('hasn.memory.list').input_schema
    # search 仅必填 query
    assert _tool('hasn.memory.search').input_schema['required'] == ['query']


def test_memory_scope_in_platform_catalog() -> None:
    """memory:write 已登记平台 scope 展示目录（webui 能力管理可见可管控）。"""
    from backend.app.mcp.platform_scopes import PLATFORM_SCOPE_CATALOG

    assert 'memory:write' in PLATFORM_SCOPE_CATALOG
    assert PLATFORM_SCOPE_CATALOG['memory:write']['domain'] == 'memory'


# ── 真实 PG 往返 ────────────────────────────────────────────────────────────────
@pytest.mark.asyncio(loop_scope='module')
async def test_memory_save_and_read_roundtrip_real_db() -> None:
    """真实 PG：save agent_self/owner/peer/world → search/recall/list 读回；owner 隔离；
    subject 规约；world 非 global 纠偏；confidence 落 [0,1]。事务真提交，测试后清理。"""
    if not await _db_reachable():
        pytest.skip('需活体 DB（DATABASE_PORT=15432）；无 DB 时跳过，不伪造')

    from sqlalchemy import delete

    from backend.app.hasn_memory.model.semantic_fact import SemanticFact
    from backend.database.db import async_db_session

    owner = f'h_mem_tool_{uuid.uuid4().hex[:18]}'
    other_owner = f'h_mem_other_{uuid.uuid4().hex[:16]}'
    agent_id = f'a_mem_{uuid.uuid4().hex[:18]}'
    ctx = _agent_ctx(owner, agent_id)
    other_ctx = _agent_ctx(other_owner, f'a_other_{uuid.uuid4().hex[:14]}')
    save = _tool('hasn.memory.save')
    search = _tool('hasn.memory.search')
    recall = _tool('hasn.memory.recall')
    listt = _tool('hasn.memory.list')
    try:
        # 1) save agent_self（默认）：subject_id 自动=agent_id，agent_id 落库，confidence 默认 0.6
        f1 = await save.execute(ctx, {'predicate': '偏好工具', 'object': 'ripgrep'})
        assert f1['subject_kind'] == 'agent_self'
        assert f1['subject_id'] == agent_id
        assert f1['agent_id'] == agent_id
        assert f1['object'] == 'ripgrep'
        assert f1['confidence'] == pytest.approx(0.6)
        assert f1['status'] == 'active'

        # 2) save owner：subject_id 自动=owner_id（不取请求体），agent_id 必空，confidence 落 [0,1]
        f2 = await save.execute(
            ctx,
            {'predicate': '称呼', 'object': '福仔', 'subject_kind': 'owner', 'confidence': 1.7},
        )
        assert f2['subject_kind'] == 'owner'
        assert f2['subject_id'] == owner
        assert f2['agent_id'] is None
        assert f2['confidence'] == pytest.approx(1.0)  # 1.7 → clamp 到 1.0

        # 3) save peer：必须给 subject_id，agent_id 必空
        peer_id = 'h_some_peer_001'
        f3 = await save.execute(
            ctx,
            {'predicate': '职业', 'object': {'role': '投资人'}, 'subject_kind': 'peer', 'subject_id': peer_id},
        )
        assert f3['subject_kind'] == 'peer'
        assert f3['subject_id'] == peer_id
        assert f3['agent_id'] is None
        assert f3['object'] == {'role': '投资人'}

        # 4) save world + 默认 global → 纠偏为 topic（表 CHECK ck_semantic_fact_world_scope）
        f4 = await save.execute(
            ctx,
            {'predicate': '常识', 'object': '北京是中国首都', 'subject_kind': 'world', 'subject_id': 'geo:beijing'},
        )
        assert f4['subject_kind'] == 'world'
        assert f4['scope_kind'] != 'global'

        # 5) peer 缺 subject_id → ValueError（不静默落库）
        with pytest.raises(ValueError, match='subject_id'):
            await save.execute(ctx, {'predicate': 'x', 'object': 'y', 'subject_kind': 'peer'})

        # 6) search：按关键词命中 predicate/object（owner 名下 active）
        hits = await search.execute(ctx, {'query': 'ripgrep'})
        assert any(h['fact_id'] == f1['fact_id'] for h in hits)
        # 限定 subject_kind=owner 只命中 owner 事实
        owner_hits = await search.execute(ctx, {'query': '福仔', 'subject_kind': 'owner'})
        assert all(h['subject_kind'] == 'owner' for h in owner_hits)
        assert any(h['fact_id'] == f2['fact_id'] for h in owner_hits)

        # 7) recall：按主体/作用域召回；min_confidence 过滤
        owner_recall = await recall.execute(ctx, {'subject_kind': 'owner'})
        assert any(r['fact_id'] == f2['fact_id'] for r in owner_recall)
        peer_recall = await recall.execute(ctx, {'subject_kind': 'peer', 'subject_id': peer_id})
        assert any(r['fact_id'] == f3['fact_id'] for r in peer_recall)
        # min_confidence=0.9 过滤掉默认 0.6 的 agent_self f1
        hi_conf = await recall.execute(ctx, {'min_confidence': 0.9})
        assert all(r['confidence'] >= 0.9 for r in hi_conf)
        assert not any(r['fact_id'] == f1['fact_id'] for r in hi_conf)

        # 8) list：分页 + total；本 owner 至少 4 条
        page = await listt.execute(ctx, {'limit': 100})
        ids = {it['fact_id'] for it in page['items']}
        assert {f1['fact_id'], f2['fact_id'], f3['fact_id'], f4['fact_id']} <= ids
        assert page['total'] >= 4
        assert page['limit'] == 100
        assert page['offset'] == 0

        # 9) owner 隔离硬边界：另一 owner 读不到本 owner 任何事实
        other_search = await search.execute(other_ctx, {'query': 'ripgrep'})
        assert other_search == []
        other_list = await listt.execute(other_ctx, {})
        assert all(it['owner_id'] == other_owner for it in other_list['items'])
        assert not (ids & {it['fact_id'] for it in other_list['items']})
    finally:
        async with async_db_session.begin() as db:
            await db.execute(delete(SemanticFact).where(SemanticFact.owner_id.in_([owner, other_owner])))
