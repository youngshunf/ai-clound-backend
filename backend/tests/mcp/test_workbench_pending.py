"""工作台未处理项聚合 真实 service 测试（禁 mock，doc05 M1）。

覆盖：
- 契约（无需 DB）：PendingScanTool 注册 + 名/命名空间/execution_location/scope 正确；
  provider 注册表含 task/plan；scope 进 catalog。
- 真实 PG 往返：seed 逾期/未来/无期限待办 + pending_approval 任务 + 未读社区通知 → aggregator.scan
  只聚逾期待办 + 待处理任务 + 未读通知，owner 隔离，deep_link canonical。事务真提交，测试后清理两个 owner。

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
    # M1 起步 task + plan；M3 横向补齐 community + workflow/deck/reel/studio + creator/quant。
    assert set(PENDING_PROVIDERS.keys()) >= {
        'task',
        'plan',
        'community',
        'workflow',
        'deck',
        'reel',
        'studio',
        'creator',
        'quant',
    }


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
    from backend.app.notification.service.notification_service import notification_service
    from backend.database.db import async_db_session
    from backend.utils.timezone import timezone

    owner = f'h_pending_{uuid.uuid4().hex[:16]}'
    other = f'h_pending_other_{uuid.uuid4().hex[:12]}'
    # creator 走平台 user_id（bigint）隔离：给 owner seed 一条 HasnHumans 映射（挑高位不撞真实行）。
    creator_uid = 990_000_000 + int(uuid.uuid4().hex[:6], 16) % 9_000_000
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

        # 2c) seed 一条未读社区通知（owner）+ 一条给 other（隔离）
        async with async_db_session.begin() as db:
            await notification_service.emit(
                db,
                recipient_id=owner,
                source={'kind': 'system', 'id': 'pending_test'},
                category='social',
                type='community_follow',
                title='有人关注了你',
                body='测试通知正文',
                payload={'preview': '测试通知正文'},
            )
            await notification_service.emit(
                db,
                recipient_id=other,
                source={'kind': 'system', 'id': 'pending_test'},
                category='social',
                type='community_follow',
                title='别人的通知',
            )

        # 2d) seed workflow/deck/reel/studio 各一条未处理项（真实写路径 / ORM 直插，零 fake）
        from backend.app.hasn_deck.service.deck_service import deck_service
        from backend.app.hasn_reel.model.reel_creation import ReelCreation
        from backend.app.hasn_studio.model.studio_artifact import StudioArtifact
        from backend.app.hasn_task.model.workflow import HasnWorkflow

        async with async_db_session.begin() as db:
            # deck：create_deck 出厂即 status='draft'（未处理项口径）
            await deck_service.create_deck(db, owner_id=owner, title='待完善草稿演示')
            # workflow：待审批（分身建定时图）；workflow_uuid unique，显式赋值
            db.add(
                HasnWorkflow(
                    workflow_uuid=f'wf_pending_{uuid.uuid4().hex[:16]}',
                    owner_id=owner,
                    name='待审批巡检工作流',
                    status='pending_approval',
                    schedule_type='cron',
                )
            )
            # reel：等你回答
            db.add(
                ReelCreation(
                    project_id=0,
                    owner_hasn_id=owner,
                    kind='agent_tools',
                    title='等你回答的短视频',
                    status='waiting_user',
                )
            )
            # studio：成品待审核
            db.add(
                StudioArtifact(
                    project_id=0,
                    owner_hasn_id=owner,
                    title='待审核成品',
                    status='reviewing',
                    origin_type='app',
                )
            )

        # 2e) seed creator（走 user_id）+ quant（走 owner_hasn_id）各一条未处理 + 各一条应排除项
        from backend.app.hasn.model.hasn_humans import HasnHumans
        from backend.app.hasn_creator.model.content import Content
        from backend.app.hasn_creator.model.project import Project
        from backend.app.hasn_quant.model.quant_backtest_run import QuantBacktestRun

        async with async_db_session.begin() as db:
            # creator 前置：owner hasn_id → creator_uid 映射行（resolve_owner_user_id 依赖）
            db.add(
                HasnHumans(
                    hasn_id=owner,
                    star_id=str(creator_uid),  # 唯一索引 idx_hasn_humans_star_id，不能用默认空串
                    user_id=creator_uid,
                    nickname='未处理项测试主人',
                    status='active',
                )
            )
            # content.project_id 有 FK 到 project 表 → 先建 project 拿 id
            proj = Project(
                project_no=f'pj_pending_{uuid.uuid4().hex[:16]}',
                user_id=creator_uid,
                owner_scope='personal',
                name='未处理项测试项目',
                status='active',
            )
            db.add(proj)
            await db.flush()
            # creator：待审核内容（review_status=pending）+ 一条已通过（应被过滤）
            # content_no 有唯一约束（默认空串会撞），显式给唯一值。
            db.add(
                Content(
                    content_no=f'ct_pending_{uuid.uuid4().hex[:16]}',
                    project_id=proj.id,
                    user_id=creator_uid,
                    owner_scope='personal',
                    title='待审核内容演示',
                    status='reviewing',
                    review_status='pending',
                )
            )
            db.add(
                Content(
                    content_no=f'ct_approved_{uuid.uuid4().hex[:16]}',
                    project_id=proj.id,
                    user_id=creator_uid,
                    owner_scope='personal',
                    title='已通过内容（不应出现）',
                    status='ready',
                    review_status='approved',
                )
            )
            # quant：回测失败（未处理）+ 回测成功（应被过滤）
            db.add(
                QuantBacktestRun(
                    owner_hasn_id=owner,
                    status='failed',
                    dataset='synthetic-oscillator-eth',
                    error='回测引擎报错（测试）',
                )
            )
            db.add(
                QuantBacktestRun(
                    owner_hasn_id=owner,
                    status='succeeded',
                    dataset='synthetic-oscillator-eth',
                )
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

        # community：聚未读通知一条，deep_link canonical /apps/community/notifications
        assert 'community' in result.by_app, result.by_app
        assert result.by_app['community'].count == 1
        assert result.by_app['community'].items[0].title == '有人关注了你'
        assert result.by_app['community'].items[0].deep_link == '/apps/community/notifications'
        assert result.by_app['community'].items[0].category == 'social'

        # workflow：待审批一条，deep_link 顶层路由 /workflows/<workflow_uuid>
        assert result.by_app['workflow'].count == 1
        wf_item = result.by_app['workflow'].items[0]
        assert wf_item.title == '待审批巡检工作流'
        assert wf_item.deep_link.startswith('/workflows/wf_pending_')
        assert wf_item.urgency == 'high'

        # deck：草稿一条，deep_link /apps/deck/<id>（云端权威 id）
        assert result.by_app['deck'].count == 1
        assert result.by_app['deck'].items[0].title == '待完善草稿演示'
        assert result.by_app['deck'].items[0].deep_link.startswith('/apps/deck/')

        # reel：等你回答一条，deep_link 应用入口 /apps/reel（无 creation 详情路由）
        assert result.by_app['reel'].count == 1
        assert result.by_app['reel'].items[0].title == '等你回答的短视频'
        assert result.by_app['reel'].items[0].deep_link == '/apps/reel'
        assert result.by_app['reel'].items[0].urgency == 'high'

        # studio：成品待审核一条，deep_link 应用入口 /apps/studio
        assert result.by_app['studio'].count == 1
        assert result.by_app['studio'].items[0].title == '待审核成品'
        assert result.by_app['studio'].items[0].deep_link == '/apps/studio'

        # creator：待审核内容一条（已通过被过滤），deep_link 应用入口 /apps/creator
        assert result.by_app['creator'].count == 1
        assert result.by_app['creator'].items[0].title == '待审核内容演示'
        assert result.by_app['creator'].items[0].deep_link == '/apps/creator'
        assert result.by_app['creator'].items[0].urgency == 'medium'

        # quant：回测失败一条（成功被过滤），deep_link 应用入口 /apps/quant
        assert result.by_app['quant'].count == 1
        assert result.by_app['quant'].items[0].deep_link == '/apps/quant'
        assert result.by_app['quant'].items[0].title.startswith('回测失败')
        assert result.by_app['quant'].items[0].urgency == 'medium'

        # 2 逾期待办 + 1 待审批任务 + 1 未读通知 + workflow/deck/reel/studio/creator/quant 各 1
        assert result.total == 10, result.by_app
        assert result.degraded == []

        # 4) owner 隔离：other 只见自己的逾期 + 自己的未读通知，绝不见 owner 的任务/待办/通知
        async with async_db_session() as db:
            other_result = await workbench_pending_aggregator.scan(db, owner_hasn_id=other)
        assert other_result.by_app.get('task') is None
        assert other_result.by_app['plan'].count == 1
        assert other_result.by_app['plan'].items[0].title == '别人的逾期'
        assert other_result.by_app['community'].count == 1
        assert other_result.by_app['community'].items[0].title == '别人的通知'

        # 5) apps 过滤：只扫 plan 时不含 task
        async with async_db_session() as db:
            plan_only = await workbench_pending_aggregator.scan(db, owner_hasn_id=owner, apps=['plan'])
        assert set(plan_only.by_app.keys()) == {'plan'}
    finally:
        async with async_db_session.begin() as db:
            await db.execute(text('DELETE FROM hasn_plan.todo WHERE owner_hasn_id = ANY(:os)'), {'os': [owner, other]})
            await db.execute(text('DELETE FROM hasn_task.task WHERE owner_id = :o'), {'o': owner})
            await db.execute(text('DELETE FROM hasn_task.workflow WHERE owner_id = :o'), {'o': owner})
            await db.execute(text('DELETE FROM hasn_deck.deck WHERE owner_id = :o'), {'o': owner})
            await db.execute(text('DELETE FROM hasn_reel.reel_creation WHERE owner_hasn_id = :o'), {'o': owner})
            await db.execute(text('DELETE FROM hasn_studio.studio_artifact WHERE owner_hasn_id = :o'), {'o': owner})
            await db.execute(text('DELETE FROM hasn_quant.quant_backtest_run WHERE owner_hasn_id = :o'), {'o': owner})
            await db.execute(text('DELETE FROM hasn_creator.content WHERE user_id = :uid'), {'uid': creator_uid})
            await db.execute(text('DELETE FROM hasn_humans WHERE hasn_id = ANY(:os)'), {'os': [owner, other]})
            await db.execute(text('DELETE FROM hasn_sync_events WHERE owner_id = ANY(:os)'), {'os': [owner, other]})
            await db.execute(text('DELETE FROM hasn_notifications WHERE target_id = ANY(:os)'), {'os': [owner, other]})
