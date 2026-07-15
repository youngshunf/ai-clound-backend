"""RC-P8 应用资源登记 hasn_artifacts 测试（doc31 §2，实施/32 RC-P8；doc35 三维度重构后更新）。

覆盖 `HasnArtifactsService.record_app_resource_artifact`：
- descriptor 驱动拼 `resource_uri=hasn://{uri_domain}/{server_id}`、`kind='resource'`（应用资源恒为
  resource——「哪个应用」由 source_app_id 答、「是什么」由 resource_kind 答，doc35 §3）、
  `resource_kind` 存 descriptor 原值、`dispatch_id` 缺省 `f"{app_id}:{server_id}"`、
  `source_kind='app'`、origin_ref 存云端权威 id；
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
    resource_kind: Mapped[str | None] = mapped_column(sa.String(64), default=None, nullable=True)
    source_app_id: Mapped[str | None] = mapped_column(sa.String(64), default=None, nullable=True)
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
    'artifact_kind': 'resource',
})

_REEL_DESCRIPTOR = ResourceDescriptor.model_validate({
    'resource_kind': 'reel.project',
    'uri_domain': 'reel/projects',
    'open': {'mode': 'internal_route', 'route_template': '/apps/reel/projects/:id'},
    'card': {'verb': '短视频', 'action_label': '打开短视频'},
    # reel 项目本体是**应用里的一个项目**（打开 = 进 /apps/reel/projects/:id），不是一个视频文件。
    # 旧值 'video' 正是「一个字段扛三个维度」的典型：它想说「产的是视频」，却把「怎么打开」答错了
    # ——UI 据 kind=video 去找 asset 播放，而这行根本没有 asset（doc35 §3.1）。
    'artifact_kind': 'resource',
})


async def _count_rows(db: AsyncSession) -> int:
    return (await db.execute(sa.select(sa.func.count()).select_from(ArtifactStub))).scalar_one()


@pytest.mark.asyncio
async def test_record_app_resource_artifact_resource_uri_and_kind(db_session: AsyncSession) -> None:
    """deck 产出 → resource_uri=hasn://deck/{id}、三维度各就各位、dispatch_id 缺省、origin_ref 存云端 id。"""
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
    # 三维度各答各的：怎么打开=resource / 是什么=deck.presentation / 哪个应用=deck / 怎么来的=app。
    assert row.kind == 'resource'
    assert row.resource_kind == 'deck.presentation'
    assert row.source_app_id == 'deck'
    assert row.source_kind == 'app'
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
    """第二应用（reel）声明 descriptor 后零改代码登记：resource_uri 用 uri_domain 多段前缀 + 三维度归位。"""
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
    assert row.kind == 'resource'
    assert row.resource_kind == 'reel.project'
    assert row.source_app_id == 'reel'
    assert row.source_kind == 'app'
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


def test_resolve_artifact_kind_always_resource() -> None:
    """应用资源恒 resource——声明与缺省同结果（doc35 §3.2）。

    旧实现按 `resource_kind` **尾段**猜 kind（`note.document`→document、`foo.bar`→other）。那是拿
    「是什么」去答「怎么打开」，猜错了 UI 就按错的方式开：知识库 `knowledge.base` 尾段 base 不在白
    名单 → 塌成 other，而 other 在 UI 里是「不知道怎么开」的坟场。现在 resource_kind 存原值、单独
    答「是什么」，kind 不再需要猜。
    """
    assert HasnArtifactsService._resolve_artifact_kind(_DECK_DESCRIPTOR) == 'resource'
    assert HasnArtifactsService._resolve_artifact_kind(_REEL_DESCRIPTOR) == 'resource'
    # 未声明 artifact_kind → 缺省同样是 resource，不再按尾段猜。
    undeclared = ResourceDescriptor.model_validate({
        'resource_kind': 'note.whatever',
        'uri_domain': 'note',
        'open': {'mode': 'entry_query', 'entry_route': '/apps/note', 'query_key': 'id'},
        'card': {'verb': '笔记', 'action_label': '打开笔记'},
    })
    assert HasnArtifactsService._resolve_artifact_kind(undeclared) == 'resource'


def test_descriptor_rejects_non_enum_artifact_kind() -> None:
    """manifest 写越界/拼错的 artifact_kind → **注册期**炸，不再静默落成 other（doc35 §1.5）。"""
    for bad in ('deck', 'dataset', 'webpage', 'other', 'vidoe'):
        with pytest.raises(ValueError):
            ResourceDescriptor.model_validate({
                'resource_kind': 'x.y',
                'uri_domain': 'x',
                'open': {'mode': 'entry_query', 'entry_route': '/apps/x', 'query_key': 'id'},
                'card': {'verb': 'X', 'action_label': '打开 X'},
                'artifact_kind': bad,
            })
