"""Publish 站点标题契约测试：分身必须给名字，主人可以随时改名。

覆盖两条本次新增的不变量：
① `normalize_title` 是唯一的标题归一入口——空/纯空白/超长一律显式报错，**不给「未命名」兜底**；
② `rename_site` 只改元数据（标题 + rev），不动 slug/可见性/当前版本指针，也不发新 revision。
"""

from __future__ import annotations

import uuid

from collections.abc import AsyncIterator

import pytest
import pytest_asyncio
import sqlalchemy as sa

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from backend.app.hasn_publish.model.site import Site
from backend.app.hasn_publish.service.publish_service import MAX_TITLE_LEN, publish_service
from backend.common.exception import errors
from backend.database.db import SQLALCHEMY_DATABASE_URL


@pytest_asyncio.fixture
async def session() -> AsyncIterator[AsyncSession]:
    """真实 PostgreSQL 会话；用例结束回滚，不污染开发库。"""
    engine = create_async_engine(SQLALCHEMY_DATABASE_URL, poolclass=NullPool)
    try:
        async with engine.connect() as connection:
            await connection.execute(sa.select(1))
    except Exception as exc:
        await engine.dispose()
        pytest.skip(f'本地 PostgreSQL 不可达，跳过: {exc!r}')

    db = async_sessionmaker(engine, expire_on_commit=False)()
    try:
        yield db
    finally:
        await db.rollback()
        await db.close()
        await engine.dispose()


def test_normalize_title_trims_and_refuses_empty() -> None:
    """去空白后必须还剩东西；空、纯空白、超长都是显式 400，不落兜底名。"""
    assert publish_service.normalize_title('  周末周边游落地页  ') == '周末周边游落地页'

    for blank in ('', '   ', '\t\n', None):
        with pytest.raises(errors.RequestError, match='标题不能为空'):
            publish_service.normalize_title(blank)

    assert publish_service.normalize_title('题' * MAX_TITLE_LEN) == '题' * MAX_TITLE_LEN
    with pytest.raises(errors.RequestError, match='不能超过'):
        publish_service.normalize_title('题' * (MAX_TITLE_LEN + 1))


@pytest.mark.asyncio
async def test_create_without_title_stores_empty_not_a_fake_name(session) -> None:
    """没给标题就存空。

    此前是 `title or '未命名'`——那个假名字在主人的列表里长得和真标题一样（黑体、不可疑），
    主人既认不出是哪个站，也看不出「这里缺一个名字」。空值才让展示层有机会降级成起名入口。
    """
    tag = uuid.uuid4().hex[:10]
    owner = f'h_publish_noname_{tag}'

    created = await publish_service.create_site(session, owner_id=owner, asset_id=f'asset_{tag}')
    assert created['site']['title'] == ''

    blank = await publish_service.create_site(
        session, owner_id=owner, title='   ', asset_id=f'asset_blank_{tag}'
    )
    assert blank['site']['title'] == '', '纯空白也存空，不留下看不见的空格标题'


@pytest.mark.asyncio
async def test_rename_site_changes_only_title_and_bumps_rev(session) -> None:
    """改名是纯元数据写：标题变、rev+1，slug/可见性/当前版本指针一律不动。"""
    tag = uuid.uuid4().hex[:10]
    owner = f'h_publish_title_{tag}'

    created = await publish_service.create_site(
        session,
        owner_id=owner,
        title='初始名字',
        asset_id=f'asset_{tag}',
    )
    site_id = created['site']['id']
    before = await session.get(Site, site_id)
    assert before is not None
    slug_before, rev_before = before.slug, before.rev
    revision_before = before.current_revision_id

    renamed = await publish_service.rename_site(
        session, owner_id=owner, site_id=site_id, title='  改名后的站点  '
    )
    assert renamed['title'] == '改名后的站点'
    assert renamed['slug'] == slug_before
    assert renamed['rev'] == rev_before + 1
    assert renamed['current_revision_id'] == revision_before
    assert renamed['visibility'] == created['site']['visibility']

    # 空名字改不动：报错且库里保持上一次的合法名字。
    with pytest.raises(errors.RequestError, match='标题不能为空'):
        await publish_service.rename_site(session, owner_id=owner, site_id=site_id, title='   ')
    await session.refresh(before)
    assert before.title == '改名后的站点'


@pytest.mark.asyncio
async def test_rename_site_is_owner_isolated(session) -> None:
    """跨 owner 改名 → 404（不泄露存在性），且原标题不受影响。"""
    tag = uuid.uuid4().hex[:10]
    owner = f'h_publish_title_own_{tag}'
    stranger = f'h_publish_title_other_{tag}'

    created = await publish_service.create_site(
        session, owner_id=owner, title='我的站点', asset_id=f'asset_{tag}'
    )
    site_id = created['site']['id']

    with pytest.raises(errors.NotFoundError):
        await publish_service.rename_site(
            session, owner_id=stranger, site_id=site_id, title='别人改的名'
        )
    row = await session.get(Site, site_id)
    assert row is not None and row.title == '我的站点'


@pytest.mark.asyncio
async def test_update_site_carries_title_and_keeps_it_when_omitted(session) -> None:
    """换内容时带 title 就改名；不带就保留原名（此前 title 在这条链路上被静默丢弃）。"""
    tag = uuid.uuid4().hex[:10]
    owner = f'h_publish_title_upd_{tag}'

    created = await publish_service.create_site(
        session, owner_id=owner, title='第一版', asset_id=f'asset_{tag}_v1'
    )
    site_id = created['site']['id']

    with_title = await publish_service.update_site(
        session,
        owner_id=owner,
        site_id=site_id,
        asset_id=f'asset_{tag}_v2',
        content_hash=f'hash_{tag}_v2',
        title='第二版名字',
    )
    assert with_title['site']['title'] == '第二版名字'

    without_title = await publish_service.update_site(
        session,
        owner_id=owner,
        site_id=site_id,
        asset_id=f'asset_{tag}_v3',
        content_hash=f'hash_{tag}_v3',
    )
    assert without_title['site']['title'] == '第二版名字'

    # 显式给空标题不是「保留原名」，是非法入参——不能靠空串把站点抹成无名。
    with pytest.raises(errors.RequestError, match='标题不能为空'):
        await publish_service.update_site(
            session,
            owner_id=owner,
            site_id=site_id,
            asset_id=f'asset_{tag}_v4',
            content_hash=f'hash_{tag}_v4',
            title='  ',
        )
