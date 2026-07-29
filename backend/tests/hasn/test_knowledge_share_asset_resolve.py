"""被分享者打开知识库文档时可解析正文图片的真实 PostgreSQL 测试。"""

from __future__ import annotations

import uuid

from collections.abc import AsyncGenerator

import pytest
import pytest_asyncio

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from backend.app.hasn.model import HasnAssetBindings, HasnAssets
from backend.app.hasn.service.authz import Subject, asset_projection
from backend.app.hasn.service.owner_storage_service import OwnerStorageService
from backend.app.hasn_knowledge.model import Document, DocumentVersion, Kb
from backend.app.hasn_knowledge.service.knowledge_service import knowledge_service
from backend.app.hasn_knowledge.service.resource_adapter import KbDocResourceAdapter
from backend.common.exception import errors
from backend.database.db import SQLALCHEMY_DATABASE_URL
from backend.utils.timezone import timezone

pytestmark = pytest.mark.asyncio


@pytest_asyncio.fixture
async def session() -> AsyncGenerator[AsyncSession, None]:
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


async def test_collect_and_gate_only_inline_assets_for_shared_document(session: AsyncSession) -> None:
    """正文资产只随有权查看的文档放行，合法 resource_ref 也不能签任意资产。"""
    tag = uuid.uuid4().hex[:8]
    owner = Subject.human(f'h_owner_{tag}')
    viewer = Subject.human(f'h_viewer_{tag}')
    inline_a = f'as_inline_a_{tag}'
    inline_b = f'as_inline_b_{tag}'
    historical = f'as_historical_{tag}'
    stranger = f'as_stranger_{tag}'

    kb = Kb(
        owner_id=owner.hasn_id,
        scope='personal',
        visibility='private',
        name='带图知识库',
        ragflow_dataset_id=f'ds_{tag}',
        embedding_model='BAAI/bge-m3',
        status='active',
    )
    session.add(kb)
    await session.flush()

    content = (
        f'![图一](hasn://asset/{inline_a})\n'
        f'![图二](hasn://asset/{inline_b})\n'
        f'重复引用 ![图一](hasn://asset/{inline_a})\n'
        '畸形引用 ![坏图](hasn://asset/)\n'
    )
    doc = Document(
        kb_id=kb.id,
        owner_id=owner.hasn_id,
        kind='native',
        name='带图文档',
        size_bytes=len(content.encode('utf-8')),
        mime_type='text/markdown',
        content=content,
        current_version=2,
        parse_status='parsed',
        source='ui',
    )
    session.add(doc)
    await session.flush()

    resource_ref = f'knowledge_doc:{doc.id}'
    resource_uri = f'hasn://knowledge/documents/{doc.id}'
    session.add_all([
        HasnAssets(
            asset_id=asset_id,
            owner_hasn_id=owner.hasn_id if asset_id != stranger else f'h_victim_{tag}',
            access='private',
            storage_id=1,
            object_key=f'knowledge/{asset_id}',
            kind='image',
            mime='image/png',
            size_bytes=10,
            lifecycle_status='active',
        )
        for asset_id in (inline_a, inline_b, historical, stranger)
    ])
    session.add_all([
        HasnAssetBindings(
            binding_id=f'bnd_{uuid.uuid4().hex}',
            owner_hasn_id=owner.hasn_id,
            asset_id=asset_id,
            resource_uri=resource_uri,
            role='inline_image',
            status='active',
        )
        for asset_id in (inline_a, inline_b, historical)
    ])
    session.add_all([
        DocumentVersion(
            document_id=doc.id,
            version_no=1,
            title='只有历史版本引用的图片',
            content=f'![旧图](hasn://asset/{historical})',
            source='ui',
        ),
        DocumentVersion(
            document_id=doc.id,
            version_no=2,
            title='当前版本',
            content=content,
            source='ui',
        ),
    ])
    await session.flush()

    inline_assets = {inline_a, inline_b, historical}
    requested = inline_assets | {stranger}

    assert await KbDocResourceAdapter().collect_asset_ids(session, str(doc.id)) == inline_assets
    assert await asset_projection.readable_asset_ids(session, viewer, resource_ref, requested) == set()

    await knowledge_service.add_share(
        session,
        subject=owner,
        kb_id=kb.id,
        grantee_type='human',
        grantee_id=viewer.hasn_id,
        permission='viewer',
    )

    assert await asset_projection.readable_asset_ids(session, viewer, resource_ref, requested) == inline_assets
    assert await asset_projection.readable_asset_ids(session, owner, resource_ref, requested) == inline_assets
    # 即使攻击者知道别人的私有 asset_id，把它写进正文但没有本文档 binding 也不能获得投影权限。
    assert stranger not in await asset_projection.readable_asset_ids(session, owner, resource_ref, requested)
    assert (
        await asset_projection.readable_asset_ids(
            session,
            owner,
            f'knowledge_doc:{doc.id + 10_000_000}',
            requested,
        )
        == set()
    )


async def test_inline_asset_write_guard_binds_actor_owned_and_rejects_resource_owner_asset(
    session: AsyncSession,
) -> None:
    """共享编辑只绑定分身主人自有图片，不能借文档 ACL 注入资源主人未绑定的私有资产。"""
    tag = uuid.uuid4().hex[:8]
    resource_owner_id = f'h_resource_owner_{tag}'
    actor_owner_id = f'h_actor_owner_{tag}'
    actor_asset = f'as_actor_{tag}'
    resource_owner_asset = f'as_resource_owner_{tag}'
    public_asset = f'as_public_{tag}'
    kb = Kb(
        owner_id=resource_owner_id,
        scope='personal',
        visibility='private',
        name='共享写点绑定测试',
        ragflow_dataset_id=f'ds_bind_{tag}',
        embedding_model='BAAI/bge-m3',
        status='active',
    )
    session.add(kb)
    await session.flush()
    doc = Document(
        kb_id=kb.id,
        owner_id=resource_owner_id,
        kind='native',
        name='绑定文档',
        size_bytes=0,
        mime_type='text/markdown',
        content='',
        current_version=1,
        parse_status='parsed',
        source='agent',
    )
    session.add(doc)
    await session.flush()
    session.add(
        DocumentVersion(
            document_id=doc.id,
            version_no=1,
            title='旧版本',
            content=f'![旧版主人图片](hasn://asset/{resource_owner_asset})',
            source='ui',
        )
    )
    session.add_all([
        HasnAssets(
            asset_id=actor_asset,
            owner_hasn_id=actor_owner_id,
            access='private',
            storage_id=1,
            object_key=f'knowledge/{actor_asset}',
            kind='image',
            mime='image/png',
            size_bytes=10,
            lifecycle_status='active',
        ),
        HasnAssets(
            asset_id=resource_owner_asset,
            owner_hasn_id=resource_owner_id,
            access='private',
            storage_id=1,
            object_key=f'knowledge/{resource_owner_asset}',
            kind='image',
            mime='image/png',
            size_bytes=10,
            lifecycle_status='active',
        ),
        HasnAssets(
            asset_id=public_asset,
            owner_hasn_id=resource_owner_id,
            access='public',
            storage_id=1,
            object_key=f'knowledge/{public_asset}',
            kind='image',
            mime='image/png',
            size_bytes=10,
            lifecycle_status='active',
        ),
    ])
    await session.flush()

    owned = await knowledge_service._authorize_inline_assets(
        session,
        actor_owner_id=actor_owner_id,
        resource_owner_id=resource_owner_id,
        content=f'![可用图](hasn://asset/{actor_asset})',
        doc_id=doc.id,
        existing_ids=set(),
    )
    await knowledge_service._bind_inline_assets(
        session,
        actor_owner_id=actor_owner_id,
        doc_id=doc.id,
        asset_ids=owned,
    )
    binding = (
        await session.execute(
            select(HasnAssetBindings).where(
                HasnAssetBindings.asset_id == actor_asset,
                HasnAssetBindings.resource_uri == f'hasn://knowledge/documents/{doc.id}',
                HasnAssetBindings.status == 'active',
            )
        )
    ).scalar_one()
    assert binding.owner_hasn_id == actor_owner_id
    assert binding.role == 'inline_image'

    with pytest.raises(errors.ForbiddenError):
        await knowledge_service._authorize_inline_assets(
            session,
            actor_owner_id=actor_owner_id,
            resource_owner_id=resource_owner_id,
            content=f'![越权图](hasn://asset/{resource_owner_asset})',
            doc_id=doc.id,
            existing_ids=set(),
        )

    assert (
        await knowledge_service._authorize_inline_assets(
            session,
            actor_owner_id=actor_owner_id,
            resource_owner_id=resource_owner_id,
            content=f'![公开图](hasn://asset/{public_asset})',
            doc_id=doc.id,
            existing_ids=set(),
        )
        == set()
    )

    # 共享 editor 原样保存不能替资源主人授权旧图片重新随文档共享。
    await knowledge_service.update_native_document(
        session,
        resource_owner_id,
        doc.id,
        title=doc.name,
        content=doc.content,
        source='ui',
        asset_actor_id=actor_owner_id,
    )
    assert (
        await session.execute(
            select(HasnAssetBindings.binding_id).where(
                HasnAssetBindings.asset_id == resource_owner_asset,
                HasnAssetBindings.resource_uri == f'hasn://knowledge/documents/{doc.id}',
                HasnAssetBindings.status == 'active',
            )
        )
    ).scalar_one_or_none() is None

    # 只有资源主人本人保存时，才可确认并回填属于自己的存量私有图片。
    await knowledge_service.update_native_document(
        session,
        resource_owner_id,
        doc.id,
        title=doc.name,
        content=doc.content,
        source='ui',
        asset_actor_id=resource_owner_id,
    )
    owner_backfill = (
        await session.execute(
            select(HasnAssetBindings).where(
                HasnAssetBindings.asset_id == resource_owner_asset,
                HasnAssetBindings.resource_uri == f'hasn://knowledge/documents/{doc.id}',
                HasnAssetBindings.status == 'active',
            )
        )
    ).scalar_one()
    assert owner_backfill.owner_hasn_id == resource_owner_id
    assert owner_backfill.role == 'inline_image'

    with pytest.raises(errors.ConflictError, match='STORAGE_INLINE_IMAGE_IN_USE'):
        await OwnerStorageService._tombstone_business_references(
            session,
            owner_hasn_id=actor_owner_id,
            asset_id=actor_asset,
            references=[
                {
                    'binding_id': binding.binding_id,
                    'resource_uri': binding.resource_uri,
                    'role': binding.role,
                    'status': binding.status,
                }
            ],
            now=timezone.now(),
        )

    await knowledge_service.delete_document(session, resource_owner_id, doc.id)
    assert (
        await session.execute(
            select(HasnAssetBindings.status).where(
                HasnAssetBindings.asset_id == actor_asset,
                HasnAssetBindings.resource_uri == f'hasn://knowledge/documents/{doc.id}',
            )
        )
    ).scalar_one() == 'deleted'
