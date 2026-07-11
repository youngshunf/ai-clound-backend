"""doc31-C register-on-write：plan 平台工具**直建**目标/计划即登记 hasn_artifacts 真实 PG 验证。

零 mock：真实本地 PostgreSQL(15432) 直调 `backend.app.mcp.tools.plan` 的 create handler
（`_h_create_goal` / `_h_create_plan`），断言 create 处即在 `public.hasn_artifacts` 落下一条
应用资源产物行——不必等工作会话完成投影（直建根本没有会话可投影），资源当场可进
「分身产物 tab / 会话资源栏」。事务回滚不污染库。需要：export DATABASE_PORT=15432。

覆盖（doc31 §32 RC-P8「直建」半场，manifest 多资源 goal/plan）：
- goal.create → `hasn://plan/goals/{id}`、plan.plan → `hasn://plan/plans/{id}`（id 即云端权威 id）；
- kind=other（manifest artifact_kind）、origin_ref=`resource:plan:{id}`、dispatch_id=`plan:{id}`、
  source_kind=tool_output、owner/agent 归属取自凭证；
- 主会话直建（无工作会话）→ session_id 为 None（产物仍凭 resource_uri 进产物 tab）；
- 幂等：同一目标重复登记只一条 active 行（UPSERT 命中就地推进，不重复登记）。
"""

from __future__ import annotations

from uuid import uuid4

import pytest
import sqlalchemy as sa

from backend.app.hasn.model.hasn_artifacts import HasnArtifacts
from backend.app.hasn.model.hasn_humans import HasnHumans
from backend.app.mcp.auth import AgentContext
from backend.app.mcp.tools.plan import _h_create_goal, _h_create_plan, _register_plan_artifact
from backend.database.db import async_db_session

pytestmark = pytest.mark.asyncio(loop_scope='module')


def _mk_ctx(owner_hasn_id: str, owner_uid: int, *, agent_name: str = '规划分身') -> AgentContext:
    """构造一个 agent 执行上下文（身份取自凭证；work_session_id 默认 None = 主会话直建）。"""
    return AgentContext(
        hasn_id=f'a_{uuid4().hex[:16]}',
        owner_id=owner_uid,
        agent_status='active',
        metadata={},
        agent_name=agent_name,
        owner_hasn_id=owner_hasn_id,
    )


async def _seed_owner(db) -> tuple[str, int]:  # noqa: ANN001
    """播种一个主人（HasnHumans），返回 (owner_hasn_id, owner_uid)。"""
    uid = 730_000_000 + (uuid4().int % 100_000_000)
    hasn_id = f'h_{uuid4().hex[:16]}'
    db.add(HasnHumans(hasn_id=hasn_id, user_id=uid, star_id=str(uid), nickname=hasn_id, status='active'))
    await db.flush()
    return hasn_id, uid


async def _artifacts_for_uri(db, *, owner_hasn_id: str, resource_uri: str) -> list[HasnArtifacts]:  # noqa: ANN001
    """查某 owner 下指定 resource_uri 的 active 产物行（用于断言登记 + 幂等）。"""
    return list(
        (
            await db.execute(
                sa.select(HasnArtifacts).where(
                    HasnArtifacts.owner_hasn_id == owner_hasn_id,
                    HasnArtifacts.resource_uri == resource_uri,
                    HasnArtifacts.status == 'active',
                )
            )
        )
        .scalars()
        .all()
    )


async def test_create_goal_registers_artifact() -> None:
    """goal.create（直建）→ hasn_artifacts 落一行，resource_uri=hasn://plan/goals/{id}、归属取自凭证。"""
    async with async_db_session() as db:
        try:
            owner, uid = await _seed_owner(db)
            ctx = _mk_ctx(owner, uid)
            goal = await _h_create_goal(db, ctx, {'title': '成为独立开发者'})
            gid = str(goal['id'])
            uri = f'hasn://plan/goals/{gid}'

            rows = await _artifacts_for_uri(db, owner_hasn_id=owner, resource_uri=uri)
            assert len(rows) == 1
            row = rows[0]
            assert row.kind == 'other'  # manifest plan.goal artifact_kind
            assert row.origin_ref == f'resource:plan:{gid}'  # 云端权威 id，非本地 id
            assert row.dispatch_id == f'plan:{gid}'  # 缺省幂等键
            assert row.source_kind == 'tool_output'
            assert row.owner_hasn_id == owner
            assert row.agent_hasn_id == ctx.agent_hasn_id  # 身份取自凭证
            assert row.title == '成为独立开发者'
            assert row.asset_id is None  # 应用资源无 asset 本体，走 resource_uri 指针
            assert row.session_id is None  # 主会话直建：无工作会话
        finally:
            await db.rollback()


async def test_create_plan_registers_artifact() -> None:
    """plan.create（默认 personal 空间·直建）→ hasn_artifacts 落一行，resource_uri=hasn://plan/plans/{id}。"""
    async with async_db_session() as db:
        try:
            owner, uid = await _seed_owner(db)
            ctx = _mk_ctx(owner, uid)
            plan = await _h_create_plan(db, ctx, {'title': '一季度出海计划'})
            assert 'id' in plan  # 默认 personal 空间不被 PE-7 拒绝
            pid = str(plan['id'])
            uri = f'hasn://plan/plans/{pid}'

            rows = await _artifacts_for_uri(db, owner_hasn_id=owner, resource_uri=uri)
            assert len(rows) == 1
            row = rows[0]
            assert row.kind == 'other'  # manifest plan.plan artifact_kind
            assert row.origin_ref == f'resource:plan:{pid}'
            assert row.dispatch_id == f'plan:{pid}'
            assert row.title == '一季度出海计划'
        finally:
            await db.rollback()


async def test_register_on_write_is_idempotent() -> None:
    """同一目标重复 register-on-write（模拟重试/多次写）→ 命中 UPSERT，仅一条 active 行、id 不变。"""
    async with async_db_session() as db:
        try:
            owner, uid = await _seed_owner(db)
            ctx = _mk_ctx(owner, uid)
            goal = await _h_create_goal(db, ctx, {'title': '写一本书'})
            gid = str(goal['id'])
            uri = f'hasn://plan/goals/{gid}'

            first = await _artifacts_for_uri(db, owner_hasn_id=owner, resource_uri=uri)
            assert len(first) == 1
            first_id = first[0].artifact_id

            # 再登记一次（同 agent + dispatch_id + resource_uri）→ 命中既有 active 行，不新增
            await _register_plan_artifact(db, ctx, ref_type='goal', result=goal)
            again = await _artifacts_for_uri(db, owner_hasn_id=owner, resource_uri=uri)
            assert len(again) == 1
            assert again[0].artifact_id == first_id
        finally:
            await db.rollback()
