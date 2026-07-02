"""工作台未处理项聚合 真实 service 测试（禁 mock，doc05 M1）。

覆盖：
- 契约（无需 DB）：PendingScanTool 注册 + 名/命名空间/execution_location/scope 正确；
  provider 注册表含 task/plan；scope 进 catalog。
- 真实 PG 往返：seed 逾期/未来/无期限待办 + pending_approval 任务 → aggregator.scan 只聚
  逾期待办 + 待处理任务，owner 隔离，deep_link canonical。事务真提交，测试后清理两个 owner。

需活体 DB（本地 15432）：
    DATABASE_PORT=15432 pytest backend/tests/mcp/test_workbench_pending.py
无 DB 时真实 PG 部分跳过（不伪造）。
"""

from __future__ import annotations

import uuid

from datetime import timedelta
from typing import TYPE_CHECKING

import pytest

from backend.app.home.service.workbench_pending_providers import PENDING_PROVIDERS
from backend.app.mcp.auth import AgentContext
from backend.app.mcp.tools.workbench import WORKBENCH_TOOLS, PendingScanTool

if TYPE_CHECKING:
    from backend.app.mcp.tools.base import BaseTool


def _agent_ctx(owner_hasn_id: str, agent_hasn_id: str = 'a_pending_test') -> AgentContext:
    return AgentContext(
        hasn_id=agent_hasn_id,
        owner_id=1,
        scopes=['workbench:pending:read'],
        agent_status='active',
        metadata={},
        agent_name='未处理项测试分身',
        owner_hasn_id=owner_hasn_id,
        session_uuid='amk_pending_test',
    )


def _task_create_tool() -> BaseTool:
    from backend.app.mcp.tools.task import TASK_TOOLS

    for t in TASK_TOOLS:
        if t.name == 'hasn.task.create':
            return t
    raise AssertionError('hasn.task.create 未注册')


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


# ── 契约（无需 DB）────────────────────────────────────────────────────────────
def test_pending_scan_tool_registered_and_scoped() -> None:
    assert PendingScanTool in WORKBENCH_TOOLS
    tool = PendingScanTool()
    assert tool.name == 'hasn.workbench.pending.scan'
    assert tool.namespace == 'hasn.workbench'
    assert tool.execution_location == 'cloud'
    assert tool.required_scopes == ['workbench:pending:read']


def test_pending_providers_registered() -> None:
    # M1 起步 task + plan（后续 M3 横向补齐其余应用）。
    assert set(PENDING_PROVIDERS.keys()) >= {'task', 'plan'}


def test_pending_scope_in_catalog() -> None:
    from backend.app.mcp.platform_scopes import PLATFORM_SCOPE_CATALOG

    assert 'workbench:pending:read' in PLATFORM_SCOPE_CATALOG
    assert PLATFORM_SCOPE_CATALOG['workbench:pending:read']['domain'] == 'workbench'


# ── 真实 PG 往返 ────────────────────────────────────────────────────────────────
@pytest.mark.asyncio(loop_scope='module')
async def test_scan_aggregates_overdue_and_tasks_real_db() -> None:
    """真实 PG：plan 只聚逾期待办、task 聚 pending_approval，owner 隔离，deep_link canonical。"""
    if not await _db_reachable():
        pytest.skip('需活体 DB（DATABASE_PORT=15432）；无 DB 时跳过，不伪造')

    from sqlalchemy import text

    from backend.app.hasn_plan.service.plan_app_service import plan_service
    from backend.app.home.service.workbench_pending_aggregator_service import workbench_pending_aggregator
    from backend.database.db import async_db_session
    from backend.utils.timezone import timezone

    owner = f'h_pending_{uuid.uuid4().hex[:16]}'
    other = f'h_pending_other_{uuid.uuid4().hex[:12]}'
    now = timezone.now()
    try:
        # 1) seed 待办：owner 2 逾期（pending+in_progress）+ 1 未来 + 1 无期限；other 1 逾期（隔离）
        async with async_db_session.begin() as db:
            await plan_service.create_todo(
                db, owner=owner, data={'title': '逾期待办A', 'status': 'pending', 'due_at': now - timedelta(days=2)}
            )
            await plan_service.create_todo(
                db,
                owner=owner,
                data={'title': '逾期待办B', 'status': 'in_progress', 'due_at': now - timedelta(hours=3)},
            )
            await plan_service.create_todo(
                db, owner=owner, data={'title': '未来待办', 'status': 'pending', 'due_at': now + timedelta(days=3)}
            )
            await plan_service.create_todo(db, owner=owner, data={'title': '无期限待办', 'status': 'pending'})
            await plan_service.create_todo(
                db, owner=other, data={'title': '别人的逾期', 'status': 'pending', 'due_at': now - timedelta(days=1)}
            )

        # 2) seed 一个 pending_approval 任务（once→scheduled 后改业务态）
        created = await _task_create_tool().execute(
            _agent_ctx(owner),
            {
                'name': '待审批巡检',
                'prompt': '检查系统健康',
                'schedule_type': 'once',
                'schedule_config': {'run_at': '2099-01-01T08:00:00+08:00'},
            },
        )
        tid = created['task_id']
        async with async_db_session.begin() as db:
            await db.execute(
                text("UPDATE hasn_task.task SET state='pending_approval' WHERE task_uuid = :u AND owner_id = :o"),
                {'u': tid, 'o': owner},
            )

        # 3) 扫描 owner
        async with async_db_session() as db:
            result = await workbench_pending_aggregator.scan(db, owner_hasn_id=owner)

        # plan：只聚逾期两条（未来 / 无期限 排除）
        assert 'plan' in result.by_app, result.by_app
        assert result.by_app['plan'].count == 2
        plan_titles = {it.title for it in result.by_app['plan'].items}
        assert plan_titles == {'逾期待办A', '逾期待办B'}
        assert all(it.deep_link == '/apps/plan' for it in result.by_app['plan'].items)
        assert all(it.urgency == 'high' for it in result.by_app['plan'].items)

        # task：聚 pending_approval 一条，deep_link canonical /apps/tasks/<id>
        assert 'task' in result.by_app, result.by_app
        assert result.by_app['task'].count == 1
        assert result.by_app['task'].items[0].deep_link == f'/apps/tasks/{tid}'

        assert result.total == 3
        assert result.degraded == []

        # 4) owner 隔离：other 只见自己的逾期，绝不见 owner 的任务/待办
        async with async_db_session() as db:
            other_result = await workbench_pending_aggregator.scan(db, owner_hasn_id=other)
        assert other_result.by_app.get('task') is None
        assert other_result.by_app['plan'].count == 1
        assert other_result.by_app['plan'].items[0].title == '别人的逾期'

        # 5) apps 过滤：只扫 plan 时不含 task
        async with async_db_session() as db:
            plan_only = await workbench_pending_aggregator.scan(db, owner_hasn_id=owner, apps=['plan'])
        assert set(plan_only.by_app.keys()) == {'plan'}
    finally:
        async with async_db_session.begin() as db:
            await db.execute(text('DELETE FROM hasn_plan.todo WHERE owner_hasn_id = ANY(:os)'), {'os': [owner, other]})
            await db.execute(text('DELETE FROM hasn_task.task WHERE owner_id = :o'), {'o': owner})
            await db.execute(text('DELETE FROM hasn_sync_events WHERE owner_id = :o'), {'o': owner})
