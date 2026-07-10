"""RC-P8 应用资源登记 hasn_artifacts 测试（doc31 §2，实施/32 RC-P8）。

覆盖 `HasnArtifactsService.record_app_resource_artifact`：
- descriptor 驱动拼 `resource_uri=hasn://{uri_domain}/{server_id}`、`kind=artifact_kind`（缺省归一）、
  `dispatch_id` 缺省 `f"{app_id}:{server_id}"`、`source_kind='tool_output'`、origin_ref 存云端权威 id；
- 幂等：应用资源无 asset_id，按 (agent, dispatch_id, resource_uri) 去重，重复调不重复登记；
- 纯 helper（`_app_id_from_descriptor`/`_resolve_artifact_kind`）的边界。

真表用 PostgreSQL JSONB，与 SQLite 不兼容 → 沿用 conftest 的策略：孤立 SQLite 友好 stub 镜像
`HasnArtifacts` 的相关列，monkeypatch 被测 service 的 module-level ORM 引用（真实跑 SQL 去重/插入路径）。
"""

from __future__ import annotations

from collections.abc import AsyncIterator

import pytest
import pytest_asyncio
import sqlalchemy as sa

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

from backend.app.hasn.schema.resource_descriptor import ResourceDescriptor
from backend.app.hasn.service.hasn_artifacts_service import HasnArtifactsService


class _ArtifactBase(DeclarativeBase):
    """孤立 declarative base，仅供本测试模块内使用。"""


class ArtifactStub(_ArtifactBase):
    """SQLite 友好的 HasnArtifacts 镜像（仅 record_app_resource_artifact 触及的列）。"""

    __tablename__ = 'hasn_artifacts'

    # SQLite ROWID 仅对 INTEGER PRIMARY KEY 自动 autoincrement。
    id: Mapped[int] = mapped_column(sa.Integer, primary_key=True, autoincrement=True)
    artifact_id: Mapped[str] = mapped_column(sa.String(40), default='')
    agent_hasn_id: Mapped[str] = mapped_column(sa.String(40), default='')
    owner_hasn_id: Mapped[str] = mapped_column(sa.String(40), default='')
    kind: Mapped[str] = mapped_column(sa.String(16), default='')
    title: Mapped[str | None] = mapped_column(sa.String(256), default=None, nullable=True)
    summary: Mapped[str | None] = mapped_column(sa.Text, default=None, nullable=True)
    body: Mapped[str | None] = mapped_column(sa.Text, default=None, nullable=True)
    asset_id: Mapped[str | None] = mapped_column(sa.String(40), default=None, nullable=True)
    resource_uri: Mapped[str | None] = mapped_column(sa.String(512), default=None, nullable=True)
    origin_ref: Mapped[str | None] = mapped_column(sa.String(128), default=None, nullable=True)
    conversation_id: Mapped[str | None] = mapped_column(sa.String(64), default=None, nullable=True)
    message_id: Mapped[int | None] = mapped_column(sa.BigInteger, default=None, nullable=True)
    session_id: Mapped[str | None] = mapped_column(sa.String(40), default=None, nullable=True)
    source_tool: Mapped[str | None] = mapped_column(sa.String(128), default=None, nullable=True)
    source_kind: Mapped[str] = mapped_column(sa.String(16), default='')
    dispatch_id: Mapped[str | None] = mapped_column(sa.String(64), default=None, nullable=True)
    # 生产列名 metadata（Python 属性 meta_data）——stub 对齐属性名+列名。
    meta_data: Mapped[dict] = mapped_column('metadata', sa.JSON, default=dict)
    status: Mapped[str] = mapped_column(sa.String(16), default='')
    created_time: Mapped[str | None] = mapped_column(sa.String(32), default=None, nullable=True)


@pytest_asyncio.fixture
async def db_session(monkeypatch: pytest.MonkeyPatch) -> AsyncIterator[AsyncSession]:
    """in-memory SQLite AsyncSession + monkeypatch service 的 HasnArtifacts 为 SQLite stub。"""
    engine = create_async_engine('sqlite+aiosqlite:///:memory:', future=True)
    async with engine.begin() as conn:
        await conn.run_sync(_ArtifactBase.metadata.create_all)

    sessionmaker = async_sessionmaker(engine, expire_on_commit=False)

    import backend.app.hasn.service.hasn_artifacts_service as svc_mod

    monkeypatch.setattr(svc_mod, 'HasnArtifacts', ArtifactStub, raising=True)

    async with sessionmaker() as session:
        try:
            yield session
        finally:
            await session.rollback()

    await engine.dispose()


_DECK_DESCRIPTOR = ResourceDescriptor.model_validate({
    'resource_kind': 'deck.presentation',
    'uri_domain': 'deck',
    'open': {'mode': 'native_window', 'window': 'deck'},
    'card': {'verb': '演示文稿', 'action_label': '打开演示文稿'},
    'artifact_kind': 'deck',
})

_REEL_DESCRIPTOR = ResourceDescriptor.model_validate({
    'resource_kind': 'reel.project',
    'uri_domain': 'reel/projects',
    'open': {'mode': 'internal_route', 'route_template': '/apps/reel/projects/:id'},
    'card': {'verb': '短视频', 'action_label': '打开短视频'},
    'artifact_kind': 'video',
})


async def _count_rows(db: AsyncSession) -> int:
    return (await db.execute(sa.select(sa.func.count()).select_from(ArtifactStub))).scalar_one()


@pytest.mark.asyncio
async def test_record_app_resource_artifact_resource_uri_and_kind(db_session: AsyncSession) -> None:
    """deck 产出 → resource_uri=hasn://deck/{server_id}、kind=deck、dispatch_id 缺省、origin_ref 存云端 id。"""
    artifact_id = await HasnArtifactsService.record_app_resource_artifact(
        db_session,
        descriptor=_DECK_DESCRIPTOR,
        server_id='deck_server_9',
        session_id='sess_1',
        agent_hasn_id='a_creator',
        owner_hasn_id='h_owner',
        title='第一季度业绩回顾',
        summary='年度总结',
    )
    assert artifact_id.startswith('art_')
    row = (
        await db_session.execute(sa.select(ArtifactStub).where(ArtifactStub.artifact_id == artifact_id))
    ).scalar_one()
    assert row.resource_uri == 'hasn://deck/deck_server_9'
    assert row.kind == 'deck'
    assert row.source_kind == 'tool_output'
    assert row.dispatch_id == 'deck:deck_server_9'
    assert row.origin_ref == 'resource:deck:deck_server_9'
    assert row.session_id == 'sess_1'
    assert row.asset_id is None  # 应用资源无 asset 本体，走 resource_uri 指针


@pytest.mark.asyncio
async def test_record_app_resource_artifact_idempotent(db_session: AsyncSession) -> None:
    """重复投影同一 deck → 幂等，返回既有 id，不重复登记（无 asset_id 也去重）。"""
    first = await HasnArtifactsService.record_app_resource_artifact(
        db_session,
        descriptor=_DECK_DESCRIPTOR,
        server_id='deck_server_5',
        session_id='sess_a',
        agent_hasn_id='a_creator',
        owner_hasn_id='h_owner',
        title='X',
    )
    assert await _count_rows(db_session) == 1
    # 第二次调用（同 agent/server_id → 同 dispatch_id+resource_uri）命中去重键。
    second = await HasnArtifactsService.record_app_resource_artifact(
        db_session,
        descriptor=_DECK_DESCRIPTOR,
        server_id='deck_server_5',
        session_id='sess_a',
        agent_hasn_id='a_creator',
        owner_hasn_id='h_owner',
        title='X 改标题也不新增',
    )
    assert second == first
    assert await _count_rows(db_session) == 1


@pytest.mark.asyncio
async def test_record_second_app_zero_code_reel(db_session: AsyncSession) -> None:
    """第二应用（reel）声明 descriptor 后零改代码登记：resource_uri 用 uri_domain 多段前缀 + kind=video。"""
    artifact_id = await HasnArtifactsService.record_app_resource_artifact(
        db_session,
        descriptor=_REEL_DESCRIPTOR,
        server_id='reel_server_42',
        session_id='sess_r',
        agent_hasn_id='a_reeler',
        owner_hasn_id='h_owner',
        title='新品发布短视频',
    )
    row = (
        await db_session.execute(sa.select(ArtifactStub).where(ArtifactStub.artifact_id == artifact_id))
    ).scalar_one()
    assert row.resource_uri == 'hasn://reel/projects/reel_server_42'
    assert row.kind == 'video'
    assert row.dispatch_id == 'reel:reel_server_42'
    assert row.origin_ref == 'resource:reel:reel_server_42'


@pytest.mark.asyncio
async def test_record_distinct_servers_not_deduped(db_session: AsyncSession) -> None:
    """不同 server_id → 不同 dispatch_id/resource_uri → 各自登记（去重不误伤）。"""
    await HasnArtifactsService.record_app_resource_artifact(
        db_session,
        descriptor=_DECK_DESCRIPTOR,
        server_id='deck_a',
        session_id='s1',
        agent_hasn_id='a_creator',
        owner_hasn_id='h_owner',
        title='A',
    )
    await HasnArtifactsService.record_app_resource_artifact(
        db_session,
        descriptor=_DECK_DESCRIPTOR,
        server_id='deck_b',
        session_id='s2',
        agent_hasn_id='a_creator',
        owner_hasn_id='h_owner',
        title='B',
    )
    assert await _count_rows(db_session) == 2


def test_app_id_from_descriptor() -> None:
    """resource_kind `{app}.{kind}` → app_id 取首段；无点 → 整串；空 → 'app'。"""
    assert HasnArtifactsService._app_id_from_descriptor(_DECK_DESCRIPTOR) == 'deck'
    assert HasnArtifactsService._app_id_from_descriptor(_REEL_DESCRIPTOR) == 'reel'


def test_resolve_artifact_kind_declared_and_fallback() -> None:
    """artifact_kind 优先；缺省按 resource_kind 尾段归一；越界 → other。"""
    # 显式 artifact_kind
    assert HasnArtifactsService._resolve_artifact_kind(_DECK_DESCRIPTOR) == 'deck'
    assert HasnArtifactsService._resolve_artifact_kind(_REEL_DESCRIPTOR) == 'video'
    # 缺省：resource_kind 尾段命中白名单
    doc_desc = ResourceDescriptor.model_validate({
        'resource_kind': 'note.document',
        'uri_domain': 'note',
        'open': {'mode': 'entry_query', 'entry_route': '/apps/note', 'query_key': 'id'},
        'card': {'verb': '笔记', 'action_label': '打开笔记'},
    })
    assert HasnArtifactsService._resolve_artifact_kind(doc_desc) == 'document'
    # 缺省：尾段越界 → other
    weird = ResourceDescriptor.model_validate({
        'resource_kind': 'foo.bar',
        'uri_domain': 'foo',
        'open': {'mode': 'entry_query', 'entry_route': '/apps/foo', 'query_key': 'id'},
        'card': {'verb': '啥', 'action_label': '打开啥'},
    })
    assert HasnArtifactsService._resolve_artifact_kind(weird) == 'other'
