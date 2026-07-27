"""PLAN-P1 — 规划与目标管理（app_id=plan，模块 19）AI-Native 平台接入 真实测试（零 mock）。

覆盖 P1 云端（数据底座 + 应用注册 + 铸 scope + owner 隔离 CRUD）：

纯 Python（无 DB）：
- manifest 通过 ``validate_manifest``（含 workbench_app 一致性闸门）；进 ``_builtin_manifests``。
- 方案 A：``tools[]``/``capabilities[]`` 置空（本地工具随 P2 hasn-mcp 落地，本期不进 manifest）。
- scopes.py 登记 plan:read/:write（聚合进全局 SCOPE_CATALOG 供三态权限 UI 中文化）。
- catalog 默认绑定（AppCollab doc21）：plan → assistant 内置分身类型 + work_session_system_prompt。
- App 形态（local_tool / 手动安装 / 内联路由 / ui_kind=None）。

真实 PostgreSQL（schema hasn_plan，dev DB 已应用迁移；会话内回滚不污染）：
- ``ensure_builtin_published`` 落 manifest 行且 hash 自愈幂等。
- owner 隔离 CRUD：goal/plan/todo/event/habit 建—读—改—删全链路；跨 owner 不可见、不可改。
- 子对象归属校验：KR 挂他人 goal、flex 块挂他人 todo → NotFound。
- reschedule 自动锁定、habit 打卡 streak、preference owner 单例 upsert、today 聚合。

事实源: docs/hasn-node设计文档/19-规划与目标管理/01-规划与目标管理总体设计.md §5/§9.1/§10.2；
        docs/hasn-node设计文档/19-规划与目标管理/实施/01-分阶段实施计划与施工清单.md P1。
"""

from __future__ import annotations

import uuid

from datetime import datetime, timedelta
from datetime import timezone as _tz
from typing import TYPE_CHECKING

import pytest
import pytest_asyncio
import sqlalchemy as sa

from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

    from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.hasn.service.ai_native_app_registry import _manifest_hash, ai_native_app_registry
from backend.app.hasn.service.app_catalog_registry import app_catalog_registry
from backend.app.hasn.service.app_catalog_service import _catalog_row_from_app
from backend.app.hasn_plan.manifest import PLAN_AI_NATIVE_MANIFEST, build_plan_app
from backend.app.hasn_plan.service.plan_app_service import plan_service
from backend.app.mcp.scopes import SCOPE_CATALOG, scope_meta
from backend.common.exception import errors
from backend.database.db import SQLALCHEMY_DATABASE_URL

_READ_SCOPE = 'plan:read'
_WRITE_SCOPE = 'plan:write'
_SCHEDULE_SCOPE = 'plan:schedule'  # PLAN-P4b：Motion 自动排程（schedule/reschedule）独立 scope
_DELEGATE_SCOPE = 'plan:delegate'  # PLAN-P5：委托派发起工作会话（出厂 Allow，2026-07-05 放开委托；risk 仍 high）


# ============================ 纯 Python（无 DB）============================


def test_plan_manifest_validates() -> None:
    """manifest 通过 validate_manifest（workbench_app 已注册，scope/协作模式一致）。"""
    result = ai_native_app_registry.validate_manifest(PLAN_AI_NATIVE_MANIFEST)
    assert result.valid, f'manifest 校验失败: {result.errors}'
    assert result.manifest_hash.startswith('sha256:')


def test_plan_in_builtin_registry() -> None:
    """plan 进 _builtin_manifests，可经 get_builtin_manifest 取回；方案 A 工具/能力置空。"""
    builtin_ids = {m['app_id'] for m in ai_native_app_registry.list_builtin_apps()}
    assert 'plan' in builtin_ids
    manifest = ai_native_app_registry.get_builtin_manifest('plan')
    assert manifest['app_id'] == 'plan'
    assert manifest['version'] == '1.0.0'
    assert manifest['execution_mode'] == 'local_tool'
    assert manifest['collaboration_mode'] == 'none'
    # 方案 A：本地工具不进 tools[]/capabilities[]（随 P2 hasn-mcp 落地补全）。
    assert manifest['tools'] == []
    assert manifest['capabilities'] == []


def test_plan_scopes_registered_in_catalog() -> None:
    """scopes.py 登记 plan:read/:write/:schedule/:delegate（聚合进全局 SCOPE_CATALOG，供三态权限 UI 中文化）。"""
    for scope in (_READ_SCOPE, _WRITE_SCOPE, _SCHEDULE_SCOPE, _DELEGATE_SCOPE):
        assert scope in SCOPE_CATALOG
        assert scope_meta(scope)['domain'] == 'plan'
    assert scope_meta(_READ_SCOPE)['label'] == '查看规划数据'
    assert scope_meta(_WRITE_SCOPE)['label'] == '管理规划数据'
    assert scope_meta(_SCHEDULE_SCOPE)['label'] == '自动排程日历'
    assert scope_meta(_DELEGATE_SCOPE)['label'] == '委托分身执行'
    # 委托是高影响动作（起工作会话、耗配额）：风险标 high，与 read/write/schedule 区分。
    assert scope_meta(_DELEGATE_SCOPE)['risk'] == 'high'


def test_plan_catalog_default_agent_binding() -> None:
    """AppCollab：plan catalog 行默认承接全能助理 + 非空 work_session_system_prompt（doc21 §4.3）。

    catalog 行由 ``_catalog_row_from_app`` 从 ``_CATALOG_AGENT_DEFAULTS['plan']`` 派生；
    运行时 ``resolve_default_agent_for_app`` 据此匹配 owner 名下 builtin_agent_key=='assistant'
    的全能助理（命中即承接，否则回退主脑）。本测验证目录默认绑定数据正确，不触 DB。
    """
    row = _catalog_row_from_app(build_plan_app())
    assert row['default_agent_type'] == 'assistant'
    assert row['work_session_system_prompt'] and '参谋长' in row['work_session_system_prompt']


def test_plan_notifications_emit_declared() -> None:
    """plan 声明 notifications.emit（提醒/简报/复盘/确认卡摊给主人发卡，P4/P5 接 emit）。"""
    emit = PLAN_AI_NATIVE_MANIFEST['notifications']['emit']
    assert emit['categories'] == ['app']
    assert emit['card_message'] is True
    assert emit['display_name'] == '规划'


def test_plan_workbench_app_shape() -> None:
    """App：local_tool + 手动安装（本期不自动挂载）+ 内联路由（非新窗口）。"""
    app = build_plan_app()
    assert app.id == 'plan'
    assert app.execution_mode == 'local_tool'
    assert app.install_policy == 'manual'
    assert app.collaboration_mode == 'none'
    # PLAN-ENT PE-6：plan 升双模应用（个人 PIM + 企业日历），scope 含 enterprise。
    assert app.scope == ('personal', 'enterprise')
    assert app.entry_route == '/apps/plan'
    assert app.ui_kind is None
    # manifest.workspace_scope 必须 ⊆ workbench_app.scope（validate_manifest 闸门）。
    assert set(PLAN_AI_NATIVE_MANIFEST['workspace_scope']) <= set(app.scope)
    assert PLAN_AI_NATIVE_MANIFEST['collaboration_mode'] == app.collaboration_mode
    # 已注册到 app_catalog_registry（validate_manifest 硬前置）。
    assert any(a.id == 'plan' for a in app_catalog_registry.list())


# ============================ 真实 PostgreSQL ============================


@pytest_asyncio.fixture
async def db() -> AsyncIterator[AsyncSession]:
    engine = create_async_engine(SQLALCHEMY_DATABASE_URL, poolclass=NullPool)
    try:
        async with engine.connect() as conn:
            await conn.execute(sa.select(1))
    except Exception as exc:
        await engine.dispose()
        pytest.skip(f'本地 PostgreSQL 不可达，跳过: {exc!r}')

    session = async_sessionmaker(engine, expire_on_commit=False)()
    try:
        yield session
    finally:
        await session.rollback()  # 会话内变更回滚，不污染 dev DB
        await session.close()
        await engine.dispose()


def _owner() -> str:
    """每用例独立 owner_hasn_id，避免与并发/历史数据串扰（会话内最终回滚）。"""
    return f'h_plan_{uuid.uuid4().hex[:16]}'


@pytest.mark.asyncio
async def test_plan_builtin_published_and_self_heals(db: AsyncSession) -> None:
    """ensure_builtin_published 落 plan manifest 行；hash 与代码 manifest 一致；二次调用幂等。"""
    published = await ai_native_app_registry.ensure_builtin_published(db, 'plan')
    assert published['app_id'] == 'plan'
    assert published['status'] == 'published'
    assert published['manifest_hash'] == _manifest_hash(PLAN_AI_NATIVE_MANIFEST)

    again = await ai_native_app_registry.ensure_builtin_published(db, 'plan')
    assert again['manifest_hash'] == published['manifest_hash']


@pytest.mark.asyncio
async def test_goal_kr_plan_crud_and_progress(db: AsyncSession) -> None:
    """目标—KR—计划—里程碑全链路 + 派生进度（KR 达成率均值，不接受前端直填）。"""
    owner = _owner()
    goal = await plan_service.create_goal(
        db, owner=owner, data={'title': '年度健身', 'category': 'health', 'progress_pct': 99}
    )
    # progress_pct 派生列：建档时不被前端 99 污染（白名单剔除）。
    assert goal['progress_pct'] == 0
    gid = goal['id']

    await plan_service.create_kr(
        db,
        owner=owner,
        goal_id=gid,
        data={'metric': '体重', 'unit': 'kg', 'current_value': 75, 'target_value': 70, 'direction': 'down'},
    )
    detail = await plan_service.get_goal(db, owner=owner, pk=gid)
    assert len(detail['key_results']) == 1

    recomputed = await plan_service.recompute_goal_progress(db, owner=owner, pk=gid)
    assert 0 <= recomputed['progress_pct'] <= 100

    plan = await plan_service.create_plan(db, owner=owner, data={'goal_id': gid, 'title': '12 周训练计划'})

    # PLANFIX-5：未设定目标值(target=0)的 KR 不冒充 100%（此前误当已达成）。
    goal2 = await plan_service.create_goal(db, owner=owner, data={'title': '减重目标'})
    g2 = goal2['id']
    await plan_service.create_kr(db, owner=owner, goal_id=g2, data={'metric': '未命名指标', 'target_value': 0})
    only_unset = await plan_service.recompute_goal_progress(db, owner=owner, pk=g2)
    assert only_unset['progress_pct'] == 0, '仅未设定 KR 的目标进度应为 0，不应冒充 100%'
    # 再加一个可量化 KR：均值只算可度量项（未设定项被排除）。
    await plan_service.create_kr(
        db, owner=owner, goal_id=g2, data={'metric': '体重', 'current_value': 80, 'target_value': 80, 'direction': 'up'}
    )
    mixed = await plan_service.recompute_goal_progress(db, owner=owner, pk=g2)
    assert mixed['progress_pct'] == 100, '可量化 KR 已达成 → 100%（未设定项不拉低也不充数）'

    assert plan['goal_id'] == gid
    pid = plan['id']
    await plan_service.create_milestone(db, owner=owner, plan_id=pid, data={'title': '第 4 周体测'})
    pdetail = await plan_service.get_plan(db, owner=owner, pk=pid)
    assert len(pdetail['milestones']) == 1

    # bound_agent 平台标准列（AppCollab）。
    bound = await plan_service.set_plan_bound_agent(db, owner=owner, pk=pid, bound_agent_id='a_planner')
    assert bound['bound_agent_id'] == 'a_planner'

    await plan_service.delete_plan(db, owner=owner, pk=pid)
    with pytest.raises(errors.NotFoundError):
        await plan_service.get_plan(db, owner=owner, pk=pid)


@pytest.mark.asyncio
async def test_create_plan_auto_binds_calling_agent(db: AsyncSession) -> None:
    """分身经 agent 通道建计划缺省自动绑「调用方分身自己」（身份取自 JWT，不让分身自报）；
    要绑别的分身才显式传 bound_agent_id 覆盖；owner/webui 通道（无 default_bound_agent）不自动绑。"""
    owner = _owner()
    caller_agent = 'a_planner_self'

    # ① 分身建计划、body 不带 bound_agent_id → 自动绑到调用方分身自己。
    auto = await plan_service.create_plan(
        db, owner=owner, data={'title': '自动绑自己的计划'}, default_bound_agent=caller_agent
    )
    assert auto['bound_agent_id'] == caller_agent, '分身建计划应自动绑定调用方自己，不该让分身自报'

    # ② 分身显式传别的分身 → 覆盖，绑给指定分身（委托场景）。
    override = await plan_service.create_plan(
        db,
        owner=owner,
        data={'title': '委托给子分身的计划', 'bound_agent_id': 'a_subagent'},
        default_bound_agent=caller_agent,
    )
    assert override['bound_agent_id'] == 'a_subagent', '显式 bound_agent_id 应覆盖自动默认'

    # ③ owner/webui 通道（不传 default_bound_agent）→ 不自动绑，由主人在 UI 显式选。
    manual = await plan_service.create_plan(db, owner=owner, data={'title': '主人手动建的计划'})
    assert not manual.get('bound_agent_id'), 'owner 手动建计划不应自动绑分身'


@pytest.mark.asyncio
async def test_owner_isolation_goal_plan(db: AsyncSession) -> None:
    """跨 owner 不可见、不可改、不可删：owner A 的目标对 owner B 一律 NotFound。"""
    owner_a, owner_b = _owner(), _owner()
    goal = await plan_service.create_goal(db, owner=owner_a, data={'title': 'A 的目标'})
    gid = goal['id']

    assert await plan_service.list_goals(db, owner=owner_b) == []
    with pytest.raises(errors.NotFoundError):
        await plan_service.get_goal(db, owner=owner_b, pk=gid)
    with pytest.raises(errors.NotFoundError):
        await plan_service.update_goal(db, owner=owner_b, pk=gid, data={'title': '篡改'})
    with pytest.raises(errors.NotFoundError):
        await plan_service.delete_goal(db, owner=owner_b, pk=gid)
    # A 仍可见且未被篡改。
    assert (await plan_service.get_goal(db, owner=owner_a, pk=gid))['title'] == 'A 的目标'


@pytest.mark.asyncio
async def test_child_ownership_guard(db: AsyncSession) -> None:
    """子对象归属校验：KR 不能挂他人 goal，计划不能挂他人 goal。"""
    owner_a, owner_b = _owner(), _owner()
    goal_a = await plan_service.create_goal(db, owner=owner_a, data={'title': 'A 的目标'})
    with pytest.raises(errors.NotFoundError):
        await plan_service.create_kr(db, owner=owner_b, goal_id=goal_a['id'], data={'metric': 'x', 'target_value': 1})
    with pytest.raises(errors.NotFoundError):
        await plan_service.create_plan(db, owner=owner_b, data={'goal_id': goal_a['id'], 'title': '蹭目标'})


@pytest.mark.asyncio
async def test_todo_event_reschedule_lock(db: AsyncSession) -> None:
    """待办 + 时间块 + flex 块归属（todo_id 必须属本人）+ 拖动改期自动锁定。"""
    owner = _owner()
    todo = await plan_service.create_todo(
        db, owner=owner, data={'title': '写周报', 'actor': 'owner', 'status': 'inbox', 'priority': 3}
    )
    tid = todo['id']

    start = datetime(2026, 6, 20, 9, 0, tzinfo=_tz.utc)
    end = start + timedelta(hours=1)
    event = await plan_service.create_event(
        db, owner=owner, data={'title': '写周报', 'kind': 'flex', 'todo_id': tid, 'start_at': start, 'end_at': end}
    )
    assert event['todo_id'] == tid
    assert event['locked'] is False

    new_start = start + timedelta(hours=2)
    new_end = new_start + timedelta(hours=1)
    rescheduled = await plan_service.reschedule_event(
        db, owner=owner, pk=event['id'], start_at=new_start, end_at=new_end
    )
    assert rescheduled['locked'] is True  # 拖动 = 用户意图，自动锁定（Motion 语义）

    with pytest.raises(errors.RequestError):
        await plan_service.reschedule_event(db, owner=owner, pk=event['id'], start_at=new_end, end_at=new_start)

    # flex 块不能挂他人待办。
    other = _owner()
    with pytest.raises(errors.NotFoundError):
        await plan_service.create_event(
            db, owner=other, data={'title': 'x', 'kind': 'flex', 'todo_id': tid, 'start_at': start, 'end_at': end}
        )


@pytest.mark.asyncio
async def test_habit_checkin_streak(db: AsyncSession) -> None:
    """习惯打卡：一天一卡去重 + streak/best_streak 累计。"""
    owner = _owner()
    habit = await plan_service.create_habit(
        db, owner=owner, data={'title': '每日阅读', 'cadence': 'daily', 'target_count': 1}
    )
    hid = habit['id']

    r1 = await plan_service.checkin_habit(db, owner=owner, habit_id=hid, data={'checkin_date': '2026-06-18'})
    assert r1['streak'] == 1
    r2 = await plan_service.checkin_habit(db, owner=owner, habit_id=hid, data={'checkin_date': '2026-06-19'})
    assert r2['streak'] == 2
    # 同一天重复打卡幂等去重（仍是 2）。
    r2b = await plan_service.checkin_habit(db, owner=owner, habit_id=hid, data={'checkin_date': '2026-06-19'})
    assert r2b['streak'] == 2
    assert r2b['best_streak'] == 2

    with pytest.raises(errors.RequestError):
        await plan_service.checkin_habit(db, owner=owner, habit_id=hid, data={'note': '忘了写日期'})


@pytest.mark.asyncio
async def test_preference_singleton_upsert(db: AsyncSession) -> None:
    """排程偏好 owner 单例：先 upsert 建，再 upsert 改（不新增行）。"""
    owner = _owner()
    assert await plan_service.get_preference(db, owner=owner) == {}
    created = await plan_service.upsert_preference(
        db, owner=owner, data={'buffer_minutes': 10, 'timezone': 'Asia/Shanghai'}
    )
    assert created['buffer_minutes'] == 10
    updated = await plan_service.upsert_preference(db, owner=owner, data={'buffer_minutes': 15})
    assert updated['buffer_minutes'] == 15
    assert updated['id'] == created['id']  # 同一行（单例不新增）


@pytest.mark.asyncio
async def test_today_overview_aggregation(db: AsyncSession) -> None:
    """今日聚合：当日时间块 + scheduled/inbox 待办分流 + active 目标进度环。"""
    owner = _owner()
    day_start = datetime(2026, 6, 19, 0, 0, tzinfo=_tz.utc)
    day_end = day_start + timedelta(days=1)

    await plan_service.create_goal(db, owner=owner, data={'title': '当前目标', 'status': 'active'})
    await plan_service.create_todo(db, owner=owner, data={'title': '收件箱事项', 'status': 'inbox', 'actor': 'owner'})
    await plan_service.create_todo(
        db, owner=owner, data={'title': '已排程事项', 'status': 'scheduled', 'actor': 'owner'}
    )
    await plan_service.create_event(
        db,
        owner=owner,
        data={
            'title': '今日会议',
            'kind': 'event',
            'start_at': day_start + timedelta(hours=10),
            'end_at': day_start + timedelta(hours=11),
        },
    )

    today = await plan_service.today_overview(db, owner=owner, day_start=day_start, day_end=day_end)
    assert len(today['events']) == 1
    assert len(today['inbox']) == 1
    assert len(today['scheduled_todos']) == 1
    assert len(today['goals']) == 1
