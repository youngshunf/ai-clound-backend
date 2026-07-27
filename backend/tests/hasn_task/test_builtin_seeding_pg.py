"""内置定时任务体系 M4 云端逻辑真实 PG 验收（零 mock，事务末尾回滚）。

设计事实源：docs/hasn-node设计文档/12-任务系统实施方案/08-内置定时任务体系与内置Agent设计.md

覆盖：
- §5 reconcile_builtin_agents：注册时建齐 builtin=true 模板分身（主脑 assistant 由 ensure_default_agent 建，
  reconcile 跳过；专业分身 content_operator / analyst 经真实 register_hasn_agent 建并 stamp builtin_agent_key）。
- §6.1 seed_builtin_tasks（INSERT-only，经 save_task_event 走权威 upsert + hasn_sync_events feed）：
  daily_briefing 绑主脑（target NULL 回退）、daily_hot_topic 绑 content_operator（按类型匹配）、
  enabled=catalog.default_enabled（daily_briefing True / 其余 False）、created_by_kind='builtin'。
  全部内置条目都播种（不按 catalog.enabled 过滤）：内置任务人人都有，growth_*/creator_*（catalog.enabled=false
  官方下线）也照样播种、初始停用、用户手动启用。每条播种发 task.created 同步事件供 daemon 拉取。
- INSERT-only 铁律：再播种零新增；用户改 enabled 不被官方覆盖。
- §6.6 可更新检测 + 手动更新：catalog revision 抬升 → builtin_update_available True →
  refresh 应用官方定义、保留 enabled/agent_id、追平 builtin_synced_revision → 可更新消失。
- §6.3 删除保护：内置任务经 owner app 面 DELETE → 403（只能停用）。
- 读路径派生：list/detail 返回 builtin_update_available 字段。

需要本地 PostgreSQL :15432（部署制品），且 M2/M3 迁移已应用、3 个 builtin 模板已 flagged。
"""

from __future__ import annotations

import uuid

from types import SimpleNamespace
from typing import TYPE_CHECKING

import httpx
import pytest
import pytest_asyncio

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from backend.app.hasn.model.hasn_agents import HasnAgents
from backend.app.hasn.model.hasn_humans import HasnHumans
from backend.app.hasn.schema.hasn_sync import ClientEvent
from backend.app.hasn.service.hasn_auth import register_hasn_agent
from backend.app.hasn.service.hasn_sync_service import hasn_sync_service
from backend.app.hasn_task.api.v1.app.task import router as app_task_router
from backend.app.hasn_task.model.builtin_catalog import HasnBuiltinTaskCatalog
from backend.app.hasn_task.model.task import HasnTask
from backend.app.hasn_task.service.builtin_seeding_service import (
    builtin_update_available,
    load_builtin_catalog_map,
    reconcile_builtin_agents,
    seed_builtin_tasks,
    update_builtin_task_from_catalog,
)
from backend.common.exception.errors import BaseExceptionError
from backend.common.security.jwt import DependsJwtAuth
from backend.database.db import SQLALCHEMY_DATABASE_URL, get_db, get_db_transaction

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

pytestmark = pytest.mark.asyncio


def _uid() -> str:
    return uuid.uuid4().hex[:8]


async def _make_owner(session) -> tuple[str, int]:
    tag = _uid()
    owner = f'h_bts_{tag}'
    uid = 5_000_000_000 + int(uuid.uuid4().int % 1_000_000_000)
    session.add(HasnHumans(hasn_id=owner, star_id=f's_{uid}', user_id=uid, nickname='BTSOwner', status='active'))
    await session.flush()
    return owner, uid


async def _bootstrap_owner_agents(session, owner: str) -> None:
    """模拟 onboarding：建主脑 assistant（stamp builtin_agent_key）+ reconcile 建齐专业内置分身。"""
    await register_hasn_agent(
        db=session,
        owner_hasn_id=owner,
        agent_name='assistant',
        display_name=f'主脑_{owner}',
        role='primary',
        builtin_agent_key='assistant',
        agent_type='cloud',
        template_id='huanxing/agent/assistant',
        created_via='onboarding',
    )
    await reconcile_builtin_agents(session, owner_id=owner)


@pytest_asyncio.fixture
async def session() -> AsyncIterator:
    engine = create_async_engine(SQLALCHEMY_DATABASE_URL, poolclass=NullPool)
    try:
        async with engine.connect() as conn:
            await conn.execute(select(1))
    except Exception as exc:
        await engine.dispose()
        pytest.skip(f'本地 PostgreSQL 不可达，跳过: {exc!r}')
    sess = async_sessionmaker(engine, expire_on_commit=False)()
    try:
        yield sess
    finally:
        await sess.rollback()
        await sess.close()
        await engine.dispose()


async def _agents_by_key(session, owner: str) -> dict[str, HasnAgents]:
    rows = (
        (await session.execute(select(HasnAgents).where(HasnAgents.owner_id == owner))).scalars().all()
    )
    return {a.builtin_agent_key: a for a in rows if a.builtin_agent_key}


async def test_reconcile_creates_specialist_builtin_agents(session) -> None:
    """reconcile 建齐 content_operator / analyst（stamp builtin_agent_key），主脑 assistant 不重复建。"""
    owner, _ = await _make_owner(session)
    await _bootstrap_owner_agents(session, owner)
    by_key = await _agents_by_key(session, owner)
    assert 'assistant' in by_key and by_key['assistant'].role == 'primary'
    assert 'content_operator' in by_key, 'reconcile 未建内容运营官'
    assert 'analyst' in by_key, 'reconcile 未建分析专家'
    # 主脑唯一（reconcile 跳过 assistant，不重复建）
    assistant_count = sum(1 for a in (await session.execute(
        select(HasnAgents).where(HasnAgents.owner_id == owner, HasnAgents.builtin_agent_key == 'assistant')
    )).scalars().all())
    assert assistant_count == 1


async def test_seed_binds_by_type_and_emits_sync_event(session) -> None:
    """播种：按类型绑定 + 回退主脑 + default_enabled + created_by_kind='builtin' + 发 task.created 同步事件。"""
    owner, _ = await _make_owner(session)
    await _bootstrap_owner_agents(session, owner)
    by_key = await _agents_by_key(session, owner)

    seeded = await seed_builtin_tasks(session, owner_id=owner)
    # 全部内置条目都播种（不再按 catalog.enabled 过滤）；初始 enabled 取 default_enabled。
    assert 'daily_briefing' in seeded, 'daily_briefing 应被播种'
    assert 'daily_hot_topic' in seeded, 'daily_hot_topic 应被播种'
    # enabled=false（官方下线）的条目也必须播种——内置任务人人都有，只是初始停用、用户手动启用。
    assert 'growth_daily_briefing' in seeded, 'growth_*（enabled=false）也必须播种'
    assert 'creator_operate' in seeded, 'creator_*（enabled=false）也必须播种'

    tasks = {
        t.builtin_key: t
        for t in (
            await session.execute(
                select(HasnTask).where(HasnTask.owner_id == owner, HasnTask.builtin_key.isnot(None))
            )
        ).scalars().all()
    }
    db = tasks['daily_briefing']
    hot = tasks['daily_hot_topic']
    # daily_briefing：target_agent_type NULL → 回退主脑；default_enabled True
    assert db.agent_id == by_key['assistant'].hasn_id, 'daily_briefing 应绑主脑'
    assert db.created_by_kind == 'builtin'
    assert db.enabled is True
    assert db.builtin_synced_revision is not None and db.builtin_synced_revision >= 1
    # daily_hot_topic：按类型绑 content_operator；default_enabled False（首播关闭）
    assert hot.agent_id == by_key['content_operator'].hasn_id, 'daily_hot_topic 应绑内容运营官'
    assert hot.enabled is False, 'daily_hot_topic 首播应 enabled=False（default_enabled）'
    assert hot.system_prompt and '内容运营官' in hot.system_prompt
    # growth_*/creator_*：catalog.enabled=false（官方下线）但仍必须播种，按当前三类内置 agent 绑定，
    # 初始 enabled=default_enabled=False（首播停用、用户手动启用即可）。
    growth = tasks['growth_daily_briefing']
    assert growth.agent_id == by_key['assistant'].hasn_id, 'growth 应绑全能助理'
    assert growth.created_by_kind == 'builtin'
    assert growth.enabled is False, 'growth 首播应停用（default_enabled=False）'
    creator = tasks['creator_operate']
    assert creator.agent_id == by_key['content_operator'].hasn_id, 'creator 应绑内容运营官'
    assert creator.enabled is False, 'creator 首播应停用（default_enabled=False）'

    # 每条播种均发 task.created 同步事件（daemon 经普通 task sync 拉取）
    evrows = (
        await session.execute(
            text(
                "SELECT payload->>'builtin_key' AS bk FROM hasn_sync_events "
                "WHERE owner_id = :o AND event_type = 'task.created' "
                "AND payload->>'builtin_key' IS NOT NULL"
            ),
            {'o': owner},
        )
    ).mappings().all()
    emitted = {r['bk'] for r in evrows}
    assert {'daily_briefing', 'daily_hot_topic'} <= emitted, f'内置任务同步事件缺失: {emitted}'


async def test_builtin_event_broadcasts_to_all_owner_nodes(session) -> None:
    """§6.2 修复：内置任务 task.created 事件按 owner 广播到 owner 名下所有节点（不被 executor-pinning 排除）。

    根因：seed 经 save_task_event 用占位 node_id='cloud-builtin-seed' 写入，会 stamp 事件 payload.node_id
    且建 assignment(executor='cloud-builtin-seed')；而 daemon 拉取时带真实 X-Node-Id，旧 pull_task_events
    的 node 可见性谓词把内置事件排除 → 内置任务下行到零个真实节点。修复加 created_by_kind='builtin' 广播分支。
    回归守卫：普通节点固定任务仍只对其 executor 节点可见（executor-pinning 不回归）。
    """
    owner, _ = await _make_owner(session)
    await _bootstrap_owner_agents(session, owner)
    await seed_builtin_tasks(session, owner_id=owner)
    by_key = await _agents_by_key(session, owner)
    gw = hasn_sync_service.gateway

    # 任意真实 daemon 节点（≠ seed 占位 'cloud-builtin-seed'）都应收到内置事件广播
    for node in ('n_deviceA', 'n_deviceB'):
        events = await gw.pull_task_events(session, owner_id=owner, node_id=node, after_revision=0, limit=200)
        bks = {e.payload.get('builtin_key') for e in events if e.payload.get('created_by_kind') == 'builtin'}
        assert {'daily_briefing', 'daily_hot_topic'} <= bks, f'节点 {node} 应收到内置任务广播，实得 {bks}'

    # 回归守卫：deviceA 创建的普通任务对 deviceB 不可见（executor-pinning 保持）
    normal_uuid = str(uuid.uuid4())
    normal_payload = {
        'task_id': normal_uuid,
        'agent_id': by_key['assistant'].hasn_id,
        'name': '普通节点固定任务',
        'description': '回归守卫',
        'prompt': 'noop',
        'schedule_type': 'cron',
        'schedule_config': {'expr': '0 9 * * *'},
        'enabled': True,
        'state': 'scheduled',
        'created_by_kind': 'owner',
    }
    await gw.save_task_event(
        session,
        owner_id=owner,
        node_id='n_deviceA',
        event=ClientEvent(
            client_event_id=f'normal_{normal_uuid}',
            event_type='task.created',
            payload={'task': normal_payload},
            hasn_id=by_key['assistant'].hasn_id,
        ),
    )

    def _uuids(events: list) -> set[str]:
        return {e.payload.get('task_uuid') or e.payload.get('task_id') for e in events}

    a_events = await gw.pull_task_events(session, owner_id=owner, node_id='n_deviceA', after_revision=0, limit=200)
    b_events = await gw.pull_task_events(session, owner_id=owner, node_id='n_deviceB', after_revision=0, limit=200)
    assert normal_uuid in _uuids(a_events), '普通任务应对其 executor 节点 deviceA 可见'
    assert normal_uuid not in _uuids(b_events), (
        '回归：普通任务不应广播到非 executor 节点 deviceB（executor-pinning 必须保持）'
    )


async def test_builtin_event_carries_cloud_numeric_server_id(session) -> None:
    """§6.6/run-now 修复：内置任务 task.created 同步事件 payload 必须带云端整型主键 server_id。

    根因：upsert 无 RETURNING，事件 payload 的 server_id 取自播种 payload（播种前不知数字 id）→ 恒 None →
    下行节点本地存 server_id=None → daemon run-now（start_task_run 要求数字 id）与 §6.6 手动更新均失败。
    修复：upsert RETURNING id 回填进事件 payload.server_id。本测断言事件 server_id == 云端 hasn_task.task.id。
    """
    owner, _ = await _make_owner(session)
    await _bootstrap_owner_agents(session, owner)
    await seed_builtin_tasks(session, owner_id=owner)
    gw = hasn_sync_service.gateway

    # 云端真实整型主键（builtin_key → id）
    rows = (
        await session.execute(
            select(HasnTask.id, HasnTask.builtin_key, HasnTask.task_uuid).where(
                HasnTask.owner_id == owner,
                HasnTask.builtin_key.isnot(None),
            )
        )
    ).all()
    id_by_uuid = {str(r.task_uuid): int(r.id) for r in rows}
    assert id_by_uuid, '应已播种内置任务并分配整型主键'
    assert all(v > 0 for v in id_by_uuid.values())

    events = await gw.pull_task_events(session, owner_id=owner, node_id='n_deviceX', after_revision=0, limit=200)
    builtin_events = [e for e in events if e.payload.get('created_by_kind') == 'builtin']
    assert builtin_events, '应能拉到内置任务 task.created 事件'
    for e in builtin_events:
        uuid_key = str(e.payload.get('task_uuid') or e.payload.get('task_id'))
        expected = id_by_uuid.get(uuid_key)
        assert expected is not None, f'事件 {uuid_key} 应对应一条云端内置任务行'
        assert e.payload.get('server_id') == expected, (
            f'内置任务 {e.payload.get("builtin_key")} 同步事件 server_id 应为云端整型主键 '
            f'{expected}，实得 {e.payload.get("server_id")}（None=run-now/§6.6 会失败）'
        )


async def test_insert_only_idempotent_and_preserves_user_edit(session) -> None:
    """INSERT-only：再播种零新增；用户改 enabled 后再播种不被官方覆盖。"""
    owner, _ = await _make_owner(session)
    await _bootstrap_owner_agents(session, owner)
    first = await seed_builtin_tasks(session, owner_id=owner)
    assert first, '首轮应有播种'

    # 用户把 daily_hot_topic 打开
    hot = (
        await session.execute(
            select(HasnTask).where(HasnTask.owner_id == owner, HasnTask.builtin_key == 'daily_hot_topic')
        )
    ).scalar_one()
    hot.enabled = True
    await session.flush()

    # 再播种：零新增（已存在跳过）
    second = await seed_builtin_tasks(session, owner_id=owner)
    assert second == [], f'INSERT-only 应零新增，实得 {second}'

    # 用户值未被覆盖
    await session.refresh(hot)
    assert hot.enabled is True, 'INSERT-only 不得覆盖用户对 enabled 的修改'


async def test_update_available_detection_and_manual_refresh(session) -> None:
    """§6.6 可更新检测 + 手动更新：抬 catalog revision → 可更新；refresh 应用官方定义+保留偏好+追平 revision。"""
    owner, _ = await _make_owner(session)
    await _bootstrap_owner_agents(session, owner)
    await seed_builtin_tasks(session, owner_id=owner)

    hot = (
        await session.execute(
            select(HasnTask).where(HasnTask.owner_id == owner, HasnTask.builtin_key == 'daily_hot_topic')
        )
    ).scalar_one()
    synced_before = hot.builtin_synced_revision
    bound_agent = hot.agent_id
    hot.enabled = True  # 用户偏好：开启
    await session.flush()

    # 抬升官方目录 revision + 改名（模拟官方更新）
    cat = (
        await session.execute(
            select(HasnBuiltinTaskCatalog).where(HasnBuiltinTaskCatalog.builtin_key == 'daily_hot_topic')
        )
    ).scalar_one()
    cat.revision = (cat.revision or 0) + 1
    cat.name = '每日热点创作 v2'
    await session.flush()

    cat_map = await load_builtin_catalog_map(session)
    assert builtin_update_available(cat_map['daily_hot_topic'], synced_before) is True, '应检测到可更新'

    updated = await update_builtin_task_from_catalog(session, owner_id=owner, task_uuid=hot.task_uuid)
    assert updated.name == '每日热点创作 v2', '应用官方最新定义'
    assert updated.builtin_synced_revision == cat.revision, '追平官方 revision'
    assert updated.enabled is True, '保留用户 enabled 偏好'
    assert updated.agent_id == bound_agent, '保留用户 agent 绑定'

    # 追平后「可更新」消失
    cat_map2 = await load_builtin_catalog_map(session)
    assert builtin_update_available(cat_map2['daily_hot_topic'], updated.builtin_synced_revision) is False


# ---------- owner app 面 HTTP（删除保护 + 读路径 builtin_update_available 字段） ----------

_APP = FastAPI()
_APP.include_router(app_task_router, prefix='/api/v1/hasn-task/app')


@_APP.exception_handler(BaseExceptionError)
async def _err_handler(_request: Request, exc: BaseExceptionError) -> JSONResponse:  # noqa: RUF029
    return JSONResponse(status_code=exc.code, content={'code': exc.code, 'msg': str(exc.msg), 'data': None})


@pytest_asyncio.fixture
async def e2e() -> AsyncIterator[SimpleNamespace]:
    engine = create_async_engine(SQLALCHEMY_DATABASE_URL, poolclass=NullPool)
    try:
        async with engine.connect() as conn:
            await conn.execute(select(1))
    except Exception as exc:
        await engine.dispose()
        pytest.skip(f'本地 PostgreSQL 不可达，跳过: {exc!r}')
    sess = async_sessionmaker(engine, expire_on_commit=False)()
    owner, uid = await _make_owner(sess)
    await _bootstrap_owner_agents(sess, owner)
    await seed_builtin_tasks(sess, owner_id=owner)

    async def _yield_session() -> AsyncIterator:  # noqa: RUF029
        yield sess

    async def _owner_auth(request: Request) -> None:  # noqa: RUF029
        request.scope['user'] = SimpleNamespace(id=uid, hasn_id=owner)

    _APP.dependency_overrides[get_db] = _yield_session
    _APP.dependency_overrides[get_db_transaction] = _yield_session
    _APP.dependency_overrides[DependsJwtAuth.dependency] = _owner_auth

    client = httpx.AsyncClient(transport=httpx.ASGITransport(app=_APP), base_url='http://e2e')
    try:
        yield SimpleNamespace(client=client, session=sess, owner=owner)
    finally:
        await client.aclose()
        _APP.dependency_overrides.clear()
        await sess.rollback()
        await sess.close()
        await engine.dispose()


async def test_builtin_task_delete_forbidden(e2e: SimpleNamespace) -> None:
    """内置任务不可删除（§6.3）：owner app 面 DELETE → 403。"""
    hot = (
        await e2e.session.execute(
            select(HasnTask).where(HasnTask.owner_id == e2e.owner, HasnTask.builtin_key == 'daily_hot_topic')
        )
    ).scalar_one()
    r = await e2e.client.delete(f'/api/v1/hasn-task/app/tasks/{hot.id}')
    assert r.status_code == 403, r.text
    body = r.json()
    assert '内置任务' in body['msg']
    # 行未删
    await e2e.session.refresh(hot)
    assert hot.deleted_at is None


async def test_detail_exposes_builtin_update_available(e2e: SimpleNamespace) -> None:
    """读路径派生：抬 catalog revision 后 detail 返回 builtin_update_available=True。"""
    hot = (
        await e2e.session.execute(
            select(HasnTask).where(HasnTask.owner_id == e2e.owner, HasnTask.builtin_key == 'daily_hot_topic')
        )
    ).scalar_one()
    # 未抬升前：无可更新
    r0 = await e2e.client.get(f'/api/v1/hasn-task/app/tasks/{hot.id}')
    assert r0.status_code == 200, r0.text
    d0 = r0.json()['data']
    assert d0['builtin_key'] == 'daily_hot_topic'
    assert d0['builtin_update_available'] is False

    # 抬升官方 revision
    cat = (
        await e2e.session.execute(
            select(HasnBuiltinTaskCatalog).where(HasnBuiltinTaskCatalog.builtin_key == 'daily_hot_topic')
        )
    ).scalar_one()
    cat.revision = (cat.revision or 0) + 1
    await e2e.session.flush()

    r1 = await e2e.client.get(f'/api/v1/hasn-task/app/tasks/{hot.id}')
    d1 = r1.json()['data']
    assert d1['builtin_update_available'] is True, 'detail 应派生出可更新'


async def test_list_exposes_builtin_update_available(e2e: SimpleNamespace) -> None:
    """读路径列表：GET /tasks 必须 200（回归守卫——paging_data 返回 dict，handler 误用
    `page_data.items`（=内建方法 dict.items，不可迭代）曾致 500、daemon 吞 500 后
    builtin_update_available 静默恒 false。修复为 `page_data['items']` 后此测应过）。
    同时断言抬升 catalog revision 后列表里对应内置任务派生出可更新。"""
    # 未抬升：列表 200 且内置任务 builtin_update_available=False（关键：此前会 500）
    r0 = await e2e.client.get('/api/v1/hasn-task/app/tasks')
    assert r0.status_code == 200, r0.text
    page0 = r0.json()['data']
    items0 = {it['builtin_key']: it for it in page0['items'] if it.get('builtin_key')}
    assert 'daily_hot_topic' in items0, '列表应含内置任务'
    assert items0['daily_hot_topic']['builtin_update_available'] is False

    # 抬升官方 revision → 列表里该内置任务派生出可更新
    cat = (
        await e2e.session.execute(
            select(HasnBuiltinTaskCatalog).where(HasnBuiltinTaskCatalog.builtin_key == 'daily_hot_topic')
        )
    ).scalar_one()
    cat.revision = (cat.revision or 0) + 1
    await e2e.session.flush()

    r1 = await e2e.client.get('/api/v1/hasn-task/app/tasks')
    assert r1.status_code == 200, r1.text
    items1 = {it['builtin_key']: it for it in r1.json()['data']['items'] if it.get('builtin_key')}
    assert items1['daily_hot_topic']['builtin_update_available'] is True, '列表应派生出可更新'
