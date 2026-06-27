"""OpenPencil design 成品分享 + M18 对外发布真实 PG 测试（doc27 §P3-C / OP-P3-9，零 mock，零新表）。

design 是 daemon 本地优先权威（云端只有轻登记表 hasn_design_project + manifest），没有云端 design_service。
故「A 成品分享」全复用既有泛型层：
- resource_share（hasn_resource_share）—— resource_type 是开放字符串，design 资源类型直接通行（授权/校验/撤销）。
- M18 web publish（publish_service）—— VALID_KINDS 加 'design'，design 成品可建 Site 出 /s/{slug}。

直接对真实 PG flush（不 commit）→ 断言 → rollback。验证：
① _validate_kind('design') 保留不被降级为 'other'（本次 VALID_KINDS 改动的核心）；
② create_site(kind='design') 真落 design kind 的 Site；
③ resource_share 对 resource_type='design' 授权后，被授权人/分身过闸；撤销后不过闸（泛型 ACL 对 design 生效）。
"""

from __future__ import annotations

import uuid

from typing import TYPE_CHECKING

import pytest
import pytest_asyncio

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

    from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.hasn.model import HasnAssets
from backend.app.hasn.service.resource_share_service import ResourceShareService
from backend.app.hasn_publish.service.publish_service import VALID_KINDS, PublishService, publish_service
from backend.database.db import SQLALCHEMY_DATABASE_URL

pytestmark = pytest.mark.asyncio

RESOURCE_TYPE_DESIGN = 'design'


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


async def _public_storage_id(session: AsyncSession) -> int | None:
    row = (await session.execute(text("select id from s3_storage where access='public' order by id limit 1"))).first()
    return int(row[0]) if row else None


async def _make_public_design_asset(session: AsyncSession, owner_hasn_id: str) -> HasnAssets:
    """落一条真实 public 设计成品资产（png 截图，storage_id 取本地 public s3 行）。"""
    storage_id = await _public_storage_id(session)
    if storage_id is None:
        pytest.skip('本地未配置 public S3 存储，跳过 create_site 断言')
    asset = HasnAssets(
        asset_id=f'ast_{uuid.uuid4().hex}',
        owner_hasn_id=owner_hasn_id,
        access='public',
        storage_id=storage_id,
        object_key=f'design-test/{uuid.uuid4().hex}.png',
        kind='image',
        mime='image/png',
        size_bytes=2048,
        extract_status='done',
    )
    session.add(asset)
    await session.flush()
    return asset


# ============================ VALID_KINDS（纯函数，本次改动核心） ============================


def test_validate_kind_accepts_design() -> None:
    """'design' 在 VALID_KINDS → _validate_kind 原样保留，不降级为 'other'。"""
    assert 'design' in VALID_KINDS
    assert PublishService._validate_kind('design') == 'design'


def test_validate_kind_unknown_falls_back_to_other() -> None:
    """未知 kind 仍兜底 'other'（回归守卫，确认没放开任意字符串）。"""
    assert PublishService._validate_kind('bogus-kind-xyz') == 'other'


# ============================ M18 对外发布（kind=design） ============================


async def test_create_site_preserves_design_kind(session: AsyncSession) -> None:
    """create_site(kind='design') → 真落 kind='design' 的 Site（source_app='design'）+ revision。"""
    tag = uuid.uuid4().hex[:8]
    owner = f'h_des_{tag}'
    asset = await _make_public_design_asset(session, owner)
    result = await publish_service.create_site(
        session,
        owner_id=owner,
        kind='design',
        title='我的矢量设计',
        asset_id=asset.asset_id,
        runtime='single-html',
        visibility='unlisted',
        source_app='design',
        source_ref=f'proj_{tag}',
    )
    site = result['site']
    assert site['kind'] == 'design'
    assert site['source_app'] == 'design' and site['source_ref'] == f'proj_{tag}'
    assert site['visibility'] == 'unlisted'
    assert site['slug']
    assert result['revision']['asset_id'] == asset.asset_id


# ============================ A 成品分享（resource_share 泛型 ACL 对 design 生效） ============================


async def test_resource_share_design_grant_and_revoke(session: AsyncSession) -> None:
    """A 把 design 项目共享给 B(editor) → B 经泛型 ACL 过闸；撤销后回落 none。"""
    tag = uuid.uuid4().hex[:8]
    owner = f'h_a_{tag}'
    grantee = f'h_b_{tag}'
    project_id = f'proj_{tag}'

    # 未授权：B 对 A 的 design 项目无权限
    perm0 = await ResourceShareService.resolve_effective_permission(
        session,
        subject_hasn_id=grantee,
        subject_kind='human',
        subject_owner_hasn_id=grantee,
        resource_type=RESOURCE_TYPE_DESIGN,
        resource_id=project_id,
        resource_owner_hasn_id=owner,
    )
    assert perm0 == 'none'

    # A 授权 B editor
    await ResourceShareService.upsert_share(
        session,
        resource_type=RESOURCE_TYPE_DESIGN,
        resource_id=project_id,
        owner_hasn_id=owner,
        grantee_type='human',
        grantee_id=grantee,
        permission='editor',
        granted_by=owner,
    )
    perm1 = await ResourceShareService.resolve_effective_permission(
        session,
        subject_hasn_id=grantee,
        subject_kind='human',
        subject_owner_hasn_id=grantee,
        resource_type=RESOURCE_TYPE_DESIGN,
        resource_id=project_id,
        resource_owner_hasn_id=owner,
    )
    assert perm1 == 'editor'

    # 共享名单含 B
    shares = await ResourceShareService.list_shares(
        session, resource_type=RESOURCE_TYPE_DESIGN, resource_id=project_id
    )
    assert any(s['grantee_id'] == grantee and s['permission'] == 'editor' for s in shares)

    # 撤销后回落 none
    await ResourceShareService.revoke_share(
        session,
        resource_type=RESOURCE_TYPE_DESIGN,
        resource_id=project_id,
        grantee_type='human',
        grantee_id=grantee,
    )
    perm2 = await ResourceShareService.resolve_effective_permission(
        session,
        subject_hasn_id=grantee,
        subject_kind='human',
        subject_owner_hasn_id=grantee,
        resource_type=RESOURCE_TYPE_DESIGN,
        resource_id=project_id,
        resource_owner_hasn_id=owner,
    )
    assert perm2 == 'none'


async def test_resource_share_design_to_agent(session: AsyncSession) -> None:
    """分享 design 项目给某分身（grantee=agent）= 能力授权 → 该分身主体过闸。"""
    tag = uuid.uuid4().hex[:8]
    owner = f'h_a_{tag}'
    agent_id = f'a_ag_{tag}'
    agent_owner = f'h_other_{tag}'  # 别人的分身
    project_id = f'proj_{tag}'

    await ResourceShareService.upsert_share(
        session,
        resource_type=RESOURCE_TYPE_DESIGN,
        resource_id=project_id,
        owner_hasn_id=owner,
        grantee_type='agent',
        grantee_id=agent_id,
        permission='editor',
        granted_by=owner,
    )
    perm = await ResourceShareService.resolve_effective_permission(
        session,
        subject_hasn_id=agent_id,
        subject_kind='agent',
        subject_owner_hasn_id=agent_owner,
        resource_type=RESOURCE_TYPE_DESIGN,
        resource_id=project_id,
        resource_owner_hasn_id=owner,
    )
    assert perm == 'editor'
