"""短视频（reel）owner 隔离数据层真实 PG 测试（零 mock，设计 doc29 P2）。

直接调 reel_service 落 ReelProject/ReelCreation 行 → flush（不 commit）→ 断言 → rollback。
不调任何引擎（reel 引擎是本地 sidecar，云端只管权威数据）。覆盖：
- 项目 CRUD + owner 行级隔离（跨主人取不到）
- 三种发起方式建创作 + 项目归属校验
- 进度透明：sync_creation 推进 pending→running→succeeded（started_at/finished_at/video_ref/result_refs/error 透传）
- 创作历史列表 + 项目详情含创作 + 删除级联
"""

from __future__ import annotations

import uuid

from typing import TYPE_CHECKING

import pytest
import pytest_asyncio

from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

    from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.hasn_reel.service.reel_service import reel_service
from backend.common.exception import errors
from backend.database.db import SQLALCHEMY_DATABASE_URL

pytestmark = pytest.mark.asyncio


@pytest_asyncio.fixture
async def session() -> AsyncIterator[AsyncSession]:
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


def _owner() -> str:
    return f'h_owner_{uuid.uuid4().hex[:12]}'


# ============================ 项目 CRUD + owner 隔离 ============================


async def test_save_project_create_and_update(session: AsyncSession) -> None:
    owner = _owner()
    created = await reel_service.save_project(
        session, owner_hasn_id=owner, title='秋季热饮系列', description='主打温暖调性', settings={'ratio': '9:16'}
    )
    assert created['id'] > 0
    assert created['title'] == '秋季热饮系列'
    assert created['status'] == 'active'
    assert created['settings'] == {'ratio': '9:16'}

    updated = await reel_service.save_project(
        session, owner_hasn_id=owner, project_id=created['id'], title='秋季热饮·改', status='archived'
    )
    assert updated['id'] == created['id']
    assert updated['title'] == '秋季热饮·改'
    assert updated['status'] == 'archived'
    # 未传的字段不变
    assert updated['settings'] == {'ratio': '9:16'}


async def test_save_project_requires_title_on_create(session: AsyncSession) -> None:
    with pytest.raises(errors.RequestError):
        await reel_service.save_project(session, owner_hasn_id=_owner())


async def test_list_projects_owner_isolation_and_archive_filter(session: AsyncSession) -> None:
    owner_a, owner_b = _owner(), _owner()
    await reel_service.save_project(session, owner_hasn_id=owner_a, title='A1')
    a2 = await reel_service.save_project(session, owner_hasn_id=owner_a, title='A2')
    await reel_service.save_project(session, owner_hasn_id=owner_a, project_id=a2['id'], status='archived')
    await reel_service.save_project(session, owner_hasn_id=owner_b, title='B1')

    # owner_a 默认只见 active（A1），不见 archived（A2），不见 owner_b 的
    active = await reel_service.list_projects(session, owner_hasn_id=owner_a)
    titles = {p['title'] for p in active}
    assert titles == {'A1'}
    # include_archived 见 A1 + A2
    allp = await reel_service.list_projects(session, owner_hasn_id=owner_a, include_archived=True)
    assert {p['title'] for p in allp} == {'A1', 'A2'}
    # owner_b 只见自己的
    b = await reel_service.list_projects(session, owner_hasn_id=owner_b)
    assert {p['title'] for p in b} == {'B1'}


async def test_get_project_cross_owner_not_found(session: AsyncSession) -> None:
    owner_a, owner_b = _owner(), _owner()
    proj = await reel_service.save_project(session, owner_hasn_id=owner_a, title='私有项目')
    # owner_a 取得到
    got = await reel_service.get_project(session, owner_hasn_id=owner_a, project_id=proj['id'])
    assert got['id'] == proj['id']
    assert got['creations'] == []
    # owner_b 取不到（行级隔离 → NotFound，不泄露）
    with pytest.raises(errors.NotFoundError):
        await reel_service.get_project(session, owner_hasn_id=owner_b, project_id=proj['id'])


# ============================ 创作生命周期 + 三种发起 ============================


@pytest.mark.parametrize('kind', ['user_pipeline', 'agent_pipeline', 'agent_tools'])
async def test_create_creation_three_kinds(session: AsyncSession, kind: str) -> None:
    owner = _owner()
    proj = await reel_service.save_project(session, owner_hasn_id=owner, title='项目')
    creation = await reel_service.create_creation(
        session, owner_hasn_id=owner, project_id=proj['id'], kind=kind, idea='做一条介绍唤星的短视频'
    )
    assert creation['kind'] == kind
    assert creation['status'] == 'pending'
    assert creation['progress'] == 0
    assert creation['project_id'] == proj['id']
    assert creation['idea'] == '做一条介绍唤星的短视频'


async def test_create_creation_rejects_bad_kind(session: AsyncSession) -> None:
    owner = _owner()
    proj = await reel_service.save_project(session, owner_hasn_id=owner, title='项目')
    with pytest.raises(errors.RequestError):
        await reel_service.create_creation(session, owner_hasn_id=owner, project_id=proj['id'], kind='不存在的方式')


async def test_create_creation_validates_project_ownership(session: AsyncSession) -> None:
    owner_a, owner_b = _owner(), _owner()
    proj = await reel_service.save_project(session, owner_hasn_id=owner_a, title='A 的项目')
    # owner_b 不能在 owner_a 的项目下开创作（项目归属校验 → NotFound）
    with pytest.raises(errors.NotFoundError):
        await reel_service.create_creation(
            session, owner_hasn_id=owner_b, project_id=proj['id'], kind='user_pipeline'
        )


# ============================ 进度透明（sync_creation：黑盒→透明的数据层落点） ============================


async def test_sync_creation_progress_lifecycle(session: AsyncSession) -> None:
    owner = _owner()
    proj = await reel_service.save_project(session, owner_hasn_id=owner, title='项目')
    creation = await reel_service.create_creation(
        session, owner_hasn_id=owner, project_id=proj['id'], kind='user_pipeline'
    )
    cid = creation['id']

    # daemon 推进：进 running + 阶段/进度 + 回填 engine_task_id
    running = await reel_service.sync_creation(
        session, owner_hasn_id=owner, creation_id=cid, status='running', stage='生成文案', progress=20,
        engine_task_id='mpt_task_123',
    )
    assert running['status'] == 'running'
    assert running['stage'] == '生成文案'
    assert running['progress'] == 20
    assert running['engine_task_id'] == 'mpt_task_123'
    assert running['started_at'] is not None
    assert running['finished_at'] is None

    # 中间进度推进
    mid = await reel_service.sync_creation(
        session, owner_hasn_id=owner, creation_id=cid, stage='合成视频', progress=80
    )
    assert mid['stage'] == '合成视频'
    assert mid['progress'] == 80
    assert mid['status'] == 'running'  # status 未变

    # 完成：成片 + 中间产物 + 时长/分辨率
    done = await reel_service.sync_creation(
        session, owner_hasn_id=owner, creation_id=cid, status='succeeded', progress=100,
        video_ref={'kind': 'local', 'path': '/work/out.mp4', 'node_id': 'node1', 'uploaded': False},
        duration_sec=22.5, resolution='1080x1920',
        result_refs={'script': '文案内容', 'audio': '/work/voice.mp3'},
    )
    assert done['status'] == 'succeeded'
    assert done['progress'] == 100
    assert done['video_ref']['path'] == '/work/out.mp4'
    assert done['duration_sec'] == 22.5
    assert done['resolution'] == '1080x1920'
    assert done['result_refs']['script'] == '文案内容'
    assert done['finished_at'] is not None


async def test_sync_creation_failure_passes_through_error(session: AsyncSession) -> None:
    owner = _owner()
    proj = await reel_service.save_project(session, owner_hasn_id=owner, title='项目')
    creation = await reel_service.create_creation(
        session, owner_hasn_id=owner, project_id=proj['id'], kind='agent_pipeline'
    )
    failed = await reel_service.sync_creation(
        session, owner_hasn_id=owner, creation_id=creation['id'], status='failed',
        error='引擎合成失败：素材下载超时',
    )
    assert failed['status'] == 'failed'
    assert failed['error'] == '引擎合成失败：素材下载超时'  # 零 fake，透传真实错误
    assert failed['finished_at'] is not None


async def test_sync_creation_progress_clamped(session: AsyncSession) -> None:
    owner = _owner()
    proj = await reel_service.save_project(session, owner_hasn_id=owner, title='项目')
    creation = await reel_service.create_creation(
        session, owner_hasn_id=owner, project_id=proj['id'], kind='user_pipeline'
    )
    # 越界进度被夹到 [0,100]
    out = await reel_service.sync_creation(session, owner_hasn_id=owner, creation_id=creation['id'], progress=250)
    assert out['progress'] == 100


async def test_sync_creation_cross_owner_not_found(session: AsyncSession) -> None:
    owner_a, owner_b = _owner(), _owner()
    proj = await reel_service.save_project(session, owner_hasn_id=owner_a, title='项目')
    creation = await reel_service.create_creation(
        session, owner_hasn_id=owner_a, project_id=proj['id'], kind='user_pipeline'
    )
    with pytest.raises(errors.NotFoundError):
        await reel_service.sync_creation(
            session, owner_hasn_id=owner_b, creation_id=creation['id'], status='running'
        )


# ============================ 历史 + 项目详情 + 删除级联 ============================


async def test_list_creations_and_project_detail(session: AsyncSession) -> None:
    owner = _owner()
    proj = await reel_service.save_project(session, owner_hasn_id=owner, title='项目')
    c1 = await reel_service.create_creation(session, owner_hasn_id=owner, project_id=proj['id'], kind='user_pipeline')
    c2 = await reel_service.create_creation(session, owner_hasn_id=owner, project_id=proj['id'], kind='agent_tools')

    # 历史最近优先（c2 在前）
    history = await reel_service.list_creations(session, owner_hasn_id=owner, project_id=proj['id'])
    assert [c['id'] for c in history] == [c2['id'], c1['id']]

    # 项目详情含创作历史
    detail = await reel_service.get_project(session, owner_hasn_id=owner, project_id=proj['id'])
    assert len(detail['creations']) == 2


async def test_delete_project_cascades_creations(session: AsyncSession) -> None:
    owner = _owner()
    proj = await reel_service.save_project(session, owner_hasn_id=owner, title='项目')
    await reel_service.create_creation(session, owner_hasn_id=owner, project_id=proj['id'], kind='user_pipeline')

    await reel_service.delete_project(session, owner_hasn_id=owner, project_id=proj['id'])
    # 项目没了
    with pytest.raises(errors.NotFoundError):
        await reel_service.get_project(session, owner_hasn_id=owner, project_id=proj['id'])
    # 其下创作也清了
    remaining = await reel_service.list_creations(session, owner_hasn_id=owner, project_id=proj['id'])
    assert remaining == []
