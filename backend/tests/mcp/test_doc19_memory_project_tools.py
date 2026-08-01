"""doc19 S2 记忆工具面项目语境契约 + 真实 PG 往返（零 mock）。

设计事实源：docs/hasn-node设计文档/02-记忆与知识库/19-多节点记忆分层与分身自治整理设计.md
  §6.2 上下文自动带入 · §7.1 save 入参 · §7.2 读入参 · §4.3/D-21 supersedes_hint

钉死的契约：

1. **`project_id` 绝不是工具入参**。分身若能自己指定 project_id，就等于能往任意项目写记忆、
   翻任意项目的事实——项目语境必须由系统注入（`AgentContext.project_id` / ContextVar），
   四个工具的 input_schema 一律不暴露它。这条测试是越权写的静态防线；
2. save 新增可选 `supersedes_hint`；search/list 新增可选 `scope_kind`/`scope_id`；
3. 工具面归属不变：`source='platform'`、`execution_location='cloud'`、save 要 `memory:write`、
   读类无 scope（`test_memory_tools.py` 也钉了同一组，这里只做本切片的回归护栏）。

真实 PG 往返需活体 DB（本地 15432）；无 DB 时跳过，不伪造。
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


def _agent_ctx(owner_hasn_id: str, agent_hasn_id: str, project_id: str | None = None) -> AgentContext:
    ctx = AgentContext(
        hasn_id=agent_hasn_id,
        owner_id=1,
        agent_status='active',
        metadata={},
        owner_hasn_id=owner_hasn_id,
        session_uuid='amk_doc19_project_tools',
    )
    # 系统注入路径：正式链路由 streamable 从 header / 工作会话挂靠落此字段，分身够不着
    ctx.project_id = project_id
    return ctx


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


# ── 契约（无需 DB）─────────────────────────────────────────────────────────────


def test_project_id_is_never_a_tool_input() -> None:
    """越权防线：四个记忆工具的 input_schema 都不许出现 project_id。"""
    for t in MEMORY_TOOLS:
        props = t.input_schema.get('properties', {})
        assert 'project_id' not in props, (
            f'{t.name} 暴露了 project_id 入参——分身就能往别人的项目写/读记忆了（doc19 §6.2）'
        )


def test_agent_context_carries_project_id_field() -> None:
    """`AgentContext.project_id` 存在且默认 None（不在项目中工作）。"""
    ctx = _agent_ctx('h_x', 'a_x')
    assert ctx.project_id is None
    # 运行期上下文，不是凭证声明：不进 AgentTokenPayload，也就不会被 JWT 冻结成过期快照
    assert not hasattr(ctx.to_token_payload(), 'project_id')


def test_save_schema_has_optional_supersedes_hint() -> None:
    """save 新增可选 supersedes_hint（§4.3），且不进 required。"""
    schema = _tool('hasn.memory.save').input_schema
    assert 'supersedes_hint' in schema['properties']
    assert schema['required'] == ['predicate', 'object']


def test_read_tools_have_scope_filters() -> None:
    """search / recall / list 都能按 scope 过滤（§7.2）；search 与 list 是本切片新补的。"""
    for name in ('hasn.memory.search', 'hasn.memory.recall', 'hasn.memory.list'):
        props = _tool(name).input_schema['properties']
        assert 'scope_kind' in props, f'{name} 缺 scope_kind 过滤入参'
        assert 'scope_id' in props, f'{name} 缺 scope_id 过滤入参'
    # 新增入参一律可选，不改必填契约
    assert _tool('hasn.memory.search').input_schema['required'] == ['query']
    assert 'required' not in _tool('hasn.memory.list').input_schema
    assert 'required' not in _tool('hasn.memory.recall').input_schema


def test_tool_identity_unchanged() -> None:
    """本切片不许动工具归属与 scope 划分（有别的测试钉这些，这里做回归护栏）。"""
    for t in MEMORY_TOOLS:
        assert t.source == 'platform'
        assert t.namespace == 'hasn.memory'
        assert t.execution_location == 'cloud'  # type: ignore[attr-defined]
        expected = ['memory:write'] if t.name == 'hasn.memory.save' else []
        assert t.required_scopes == expected


# ── 真实 PG 往返 ────────────────────────────────────────────────────────────────


@pytest.mark.asyncio(loop_scope='session')
async def test_tool_face_project_context_roundtrip_real_db() -> None:
    """真实 PG：工具面带项目语境 save → 自动落 project；search/recall/list 做并集检索。

    覆盖：
    - 未显式给 scope → 落 project + 项目 UUID；显式给 scope → 不被覆盖；
    - 并集检索：当前项目 + 全局在，另一个项目不在；
    - supersedes_hint 经工具面落列并读回。
    """
    if not await _db_reachable():
        pytest.skip('需活体 DB（DATABASE_PORT=15432）；无 DB 时跳过，不伪造')

    from sqlalchemy import delete

    from backend.app.hasn_memory.model.semantic_fact import SemanticFact
    from backend.database.db import async_db_session

    owner = f'h_d19t_{uuid.uuid4().hex[:16]}'
    agent = f'a_d19t_{uuid.uuid4().hex[:16]}'
    here = str(uuid.uuid4())
    elsewhere = str(uuid.uuid4())
    marker = f'工具面{uuid.uuid4().hex[:6]}'

    in_project = _agent_ctx(owner, agent, here)
    in_other_project = _agent_ctx(owner, agent, elsewhere)
    no_project = _agent_ctx(owner, agent)

    save = _tool('hasn.memory.save')
    search = _tool('hasn.memory.search')
    recall = _tool('hasn.memory.recall')
    listt = _tool('hasn.memory.list')
    try:
        # 1) 项目语境 + 未给 scope → 自动落 project
        mine = await save.execute(in_project, {'predicate': '项目约定', 'object': f'{marker}·本项目走 systemd'})
        assert mine['scope_kind'] == 'project'
        assert mine['scope_id'] == here

        # 2) 项目语境 + 显式 scope → 显式优先
        explicit = await save.execute(
            in_project,
            {'predicate': '项目约定', 'object': f'{marker}·显式全局', 'scope_kind': 'global'},
        )
        assert explicit['scope_kind'] == 'global'
        assert explicit['scope_id'] != here

        # 3) 另一个项目的专属事实
        theirs = await save.execute(
            in_other_project, {'predicate': '项目约定', 'object': f'{marker}·别的项目走 docker'}
        )
        assert theirs['scope_id'] == elsewhere

        # 4) 无项目语境 → 兜底 global（既有行为不回归）
        plain = await save.execute(no_project, {'predicate': '偏好', 'object': f'{marker}·随手一记'})
        assert plain['scope_kind'] == 'global'

        # 5) 并集检索：当前项目 + 全局在，另一个项目不在
        for label, hits in (
            ('search', await search.execute(in_project, {'query': marker})),
            ('recall', await recall.execute(in_project, {'query': marker})),
            ('list', (await listt.execute(in_project, {'limit': 200}))['items']),
        ):
            ids = {h['fact_id'] for h in hits}
            assert mine['fact_id'] in ids, f'{label}: 当前项目事实必须在'
            assert explicit['fact_id'] in ids, f'{label}: 全局事实**必定**在（读不收窄）'
            assert plain['fact_id'] in ids, f'{label}: 全局事实**必定**在（读不收窄）'
            assert theirs['fact_id'] not in ids, f'{label}: 别的项目的专属事实不该串进来'

        # 6) 无项目语境读 → 全都看得到（含别的项目）
        all_ids = {h['fact_id'] for h in await search.execute(no_project, {'query': marker})}
        assert theirs['fact_id'] in all_ids

        # 7) 显式 scope 过滤优先于项目并集
        only_theirs = await search.execute(
            in_project, {'query': marker, 'scope_kind': 'project', 'scope_id': elsewhere}
        )
        assert {h['fact_id'] for h in only_theirs} == {theirs['fact_id']}

        # 8) supersedes_hint 经工具面落列 + 读回
        corrected = await save.execute(
            in_project,
            {
                'predicate': '项目约定',
                'object': f'{marker}·改用 launchd',
                'supersedes_hint': mine['fact_id'],
            },
        )
        assert corrected['supersedes_hint'] == mine['fact_id']
        by_id = {h['fact_id']: h for h in await search.execute(in_project, {'query': marker})}
        assert by_id[corrected['fact_id']]['supersedes_hint'] == mine['fact_id']
        # 只是线索，旧事实不被隐式取代（裁决在 S6 合并）
        assert by_id[mine['fact_id']]['status'] == 'active'
    finally:
        async with async_db_session.begin() as db:
            await db.execute(delete(SemanticFact).where(SemanticFact.owner_id == owner))
