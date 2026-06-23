"""统一视频引擎 studio 产物级协作 + M18 对外发布真实 PG 测试（零 mock，全复用 hasn_resource_share）。

对齐 knowledge 的 test_knowledge_resource_share：studio 项目/成品读写仍 owner_hasn_id keyed，本测试只覆盖
叠加其上的「权限闸 + 共享名单 + 可访问列表 + M18 发布」：authorize_project/authorize_artifact /
list_accessible_projects / list_accessible_artifacts / add_*_share / revoke_*_share / publish_artifact。

直接插 StudioProject/StudioArtifact/HasnAssets 行 → flush（不 commit）→ 断言 → rollback（不调真引擎、不真渲染）。
M18 发布用真实 public 资产（storage_id 取本地 s3_storage 的 public 行）验证 /s/{slug} 落地页能现解析签名 URL。
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
from backend.app.hasn_publish.api.v1.open.hosting import _content_csp_for_video, _video_landing_html
from backend.app.hasn_publish.service.publish_service import publish_service
from backend.app.hasn_studio.model import StudioArtifact, StudioProject
from backend.app.hasn_studio.service.studio_service import Subject, studio_service
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


async def _make_project(session: AsyncSession, owner_hasn_id: str, *, title: str = '视频项目') -> StudioProject:
    row = StudioProject(
        owner_hasn_id=owner_hasn_id,
        title=title,
        description=None,
        default_pipeline_key='cinematic',
        settings={},
        status='draft',
    )
    session.add(row)
    await session.flush()
    return row


async def _make_artifact(
    session: AsyncSession, owner_hasn_id: str, *, project_id: int = 0, video_asset_uri: str = '', title: str = '成片'
) -> StudioArtifact:
    row = StudioArtifact(
        project_id=project_id,
        owner_hasn_id=owner_hasn_id,
        title=title,
        pipeline_key='cinematic',
        video_asset_uri=video_asset_uri,
        resolution='1080x1920',
        status='completed',
        origin_type='app',
    )
    session.add(row)
    await session.flush()
    return row


async def _public_storage_id(session: AsyncSession) -> int | None:
    row = (await session.execute(text("select id from s3_storage where access='public' order by id limit 1"))).first()
    return int(row[0]) if row else None


async def _make_public_video_asset(session: AsyncSession, owner_hasn_id: str) -> HasnAssets:
    """落一条真实 public 视频资产（storage_id 取本地 public s3 行）；public_url 纯构造，无网络。"""
    storage_id = await _public_storage_id(session)
    if storage_id is None:
        pytest.skip('本地未配置 public S3 存储，跳过发布 URL 解析断言')
    asset = HasnAssets(
        asset_id=f'ast_{uuid.uuid4().hex}',
        owner_hasn_id=owner_hasn_id,
        access='public',
        storage_id=storage_id,
        object_key=f'studio-test/{uuid.uuid4().hex}.mp4',
        kind='video',
        mime='video/mp4',
        size_bytes=1024,
        extract_status='done',
    )
    session.add(asset)
    await session.flush()
    return asset


# ============================ 项目分享 ============================


async def test_project_share_human_viewer_editor(session: AsyncSession) -> None:
    """A 项目 → 共享给 B(editor) → B 可读可改不可管理 → 撤销后 B 不可见。"""
    tag = uuid.uuid4().hex[:8]
    a = Subject.human(f'h_a_{tag}')
    b = Subject.human(f'h_b_{tag}')
    project = await _make_project(session, a.hasn_id)

    with pytest.raises(errors.NotFoundError):
        await studio_service.authorize_project(session, subject=b, project_id=project.id, need='viewer')

    await studio_service.add_project_share(
        session, subject=a, project_id=project.id, grantee_type='human', grantee_id=b.hasn_id, permission='editor'
    )
    got = await studio_service.authorize_project(session, subject=b, project_id=project.id, need='editor')
    assert got.owner_hasn_id == a.hasn_id  # 委托键 = 项目主人
    with pytest.raises(errors.ForbiddenError):
        await studio_service.authorize_project(session, subject=b, project_id=project.id, need='manager')

    assert (
        await studio_service.revoke_project_share(
            session, subject=a, project_id=project.id, grantee_type='human', grantee_id=b.hasn_id
        )
        is True
    )
    with pytest.raises(errors.NotFoundError):
        await studio_service.authorize_project(session, subject=b, project_id=project.id, need='viewer')


async def test_viewer_cannot_edit_or_dispatch_render(session: AsyncSession) -> None:
    """viewer 协作者：能读，不能改（editor 闸）、不能派渲染（render 走 editor 闸）。"""
    tag = uuid.uuid4().hex[:8]
    a = Subject.human(f'h_a_{tag}')
    b = Subject.human(f'h_b_{tag}')
    project = await _make_project(session, a.hasn_id)
    await studio_service.add_project_share(
        session, subject=a, project_id=project.id, grantee_type='human', grantee_id=b.hasn_id, permission='viewer'
    )
    # viewer 过 viewer 闸
    await studio_service.authorize_project(session, subject=b, project_id=project.id, need='viewer')
    # viewer 不过 editor 闸（保存/派渲染都至少 editor）
    with pytest.raises(errors.ForbiddenError):
        await studio_service.authorize_project(session, subject=b, project_id=project.id, need='editor')


async def test_list_accessible_projects_union(session: AsyncSession) -> None:
    """list_accessible_projects：B 的列表含「我的」(owner/manager) + 「共享给我的」(shared/viewer)。"""
    tag = uuid.uuid4().hex[:8]
    a = Subject.human(f'h_a_{tag}')
    b = Subject.human(f'h_b_{tag}')
    own = await _make_project(session, b.hasn_id, title='B 自己的')
    shared = await _make_project(session, a.hasn_id, title='A 共享给 B 的')
    await studio_service.add_project_share(
        session, subject=a, project_id=shared.id, grantee_type='human', grantee_id=b.hasn_id, permission='viewer'
    )

    items = await studio_service.list_accessible_projects(session, subject=b)
    by_id = {it['id']: it for it in items}
    assert by_id[own.id]['relation'] == 'owner' and by_id[own.id]['my_permission'] == 'manager'
    assert by_id[shared.id]['relation'] == 'shared' and by_id[shared.id]['my_permission'] == 'viewer'


async def test_manager_can_reshare_project(session: AsyncSession) -> None:
    """被授 manager 的协作者可管理项目共享名单；viewer 不可。"""
    tag = uuid.uuid4().hex[:8]
    a = Subject.human(f'h_a_{tag}')
    b = Subject.human(f'h_b_{tag}')
    c = Subject.human(f'h_c_{tag}')
    project = await _make_project(session, a.hasn_id)
    await studio_service.add_project_share(
        session, subject=a, project_id=project.id, grantee_type='human', grantee_id=b.hasn_id, permission='manager'
    )
    # B(manager) 可再加 C
    await studio_service.add_project_share(
        session, subject=b, project_id=project.id, grantee_type='human', grantee_id=c.hasn_id, permission='viewer'
    )
    shares = await studio_service.list_project_shares(session, subject=b, project_id=project.id)
    grantees = {(s['grantee_type'], s['grantee_id']) for s in shares['shares']}
    assert ('human', b.hasn_id) in grantees
    assert ('human', c.hasn_id) in grantees
    # C(viewer) 不可管理共享名单
    with pytest.raises(errors.ForbiddenError):
        await studio_service.list_project_shares(session, subject=c, project_id=project.id)


async def test_share_project_to_agent_grants_tool_access(session: AsyncSession) -> None:
    """分享项目给某分身（grantee=agent）= 能力授权 → 该分身主体过闸 + 出现在其可访问列表。"""
    tag = uuid.uuid4().hex[:8]
    a = Subject.human(f'h_a_{tag}')
    agent = Subject.agent(f'a_ag_{tag}', owner_hasn_id=f'h_other_{tag}')  # 别人的分身
    project = await _make_project(session, a.hasn_id)

    with pytest.raises(errors.NotFoundError):
        await studio_service.authorize_project(session, subject=agent, project_id=project.id, need='viewer')

    await studio_service.add_project_share(
        session, subject=a, project_id=project.id, grantee_type='agent', grantee_id=agent.hasn_id, permission='editor'
    )
    # 分身能力授权后：可过 editor 闸（hasn.studio.* 据此可操作该项目）
    got = await studio_service.authorize_project(session, subject=agent, project_id=project.id, need='editor')
    assert got.id == project.id
    # 列表也含（agent 主体经 _shared_ids_for_agent）
    items = await studio_service.list_accessible_projects(session, subject=agent)
    assert any(it['id'] == project.id and it['relation'] == 'shared' for it in items)


# ============================ 成品分享 ============================


async def test_artifact_share_and_list_accessible(session: AsyncSession) -> None:
    """成品共享给 B(viewer) → B 过 viewer 闸 + 出现在 list_accessible_artifacts（relation=shared）。"""
    tag = uuid.uuid4().hex[:8]
    a = Subject.human(f'h_a_{tag}')
    b = Subject.human(f'h_b_{tag}')
    art = await _make_artifact(session, a.hasn_id, title='A 的成片')

    with pytest.raises(errors.NotFoundError):
        await studio_service.authorize_artifact(session, subject=b, artifact_id=art.id, need='viewer')

    await studio_service.add_artifact_share(
        session, subject=a, artifact_id=art.id, grantee_type='human', grantee_id=b.hasn_id, permission='viewer'
    )
    got = await studio_service.authorize_artifact(session, subject=b, artifact_id=art.id, need='viewer')
    assert got.owner_hasn_id == a.hasn_id
    # viewer 不可再分享（manager 闸）
    with pytest.raises(errors.ForbiddenError):
        await studio_service.list_artifact_shares(session, subject=b, artifact_id=art.id)

    items = await studio_service.list_accessible_artifacts(session, subject=b)
    by_id = {it['id']: it for it in items}
    assert by_id[art.id]['relation'] == 'shared' and by_id[art.id]['my_permission'] == 'viewer'


async def test_share_artifact_to_agent(session: AsyncSession) -> None:
    """分享成片给某分身 → 该分身过闸 + 列表含。"""
    tag = uuid.uuid4().hex[:8]
    a = Subject.human(f'h_a_{tag}')
    agent = Subject.agent(f'a_ag_{tag}', owner_hasn_id=f'h_other_{tag}')
    art = await _make_artifact(session, a.hasn_id)
    await studio_service.add_artifact_share(
        session, subject=a, artifact_id=art.id, grantee_type='agent', grantee_id=agent.hasn_id, permission='viewer'
    )
    got = await studio_service.authorize_artifact(session, subject=agent, artifact_id=art.id, need='viewer')
    assert got.id == art.id
    items = await studio_service.list_accessible_artifacts(session, subject=agent)
    assert any(it['id'] == art.id and it['relation'] == 'shared' for it in items)


# ============================ M18 对外发布（kind=video） ============================


async def test_publish_artifact_creates_video_site(session: AsyncSession) -> None:
    """publish_artifact：manager 权 → 建 kind='video' Site（source_app='studio'）+ video-landing revision。"""
    tag = uuid.uuid4().hex[:8]
    a = Subject.human(f'h_a_{tag}')
    asset = await _make_public_video_asset(session, a.hasn_id)
    art = await _make_artifact(session, a.hasn_id, video_asset_uri=f'hasn://asset/{asset.asset_id}', title='待发布成片')

    result = await studio_service.publish_artifact(session, subject=a, artifact_id=art.id, visibility='unlisted')
    site = result['site']
    assert site['kind'] == 'video'
    assert site['source_app'] == 'studio' and site['source_ref'] == str(art.id)
    assert site['visibility'] == 'unlisted'
    assert result['revision']['runtime'] == 'video-landing'
    assert result['revision']['asset_id'] == asset.asset_id  # 存成片资产本身（serve 期现解析）
    slug = site['slug']
    assert slug

    # 幂等：再次发布 → 更新同一 Site（slug 不变）
    again = await studio_service.publish_artifact(session, subject=a, artifact_id=art.id, visibility='public')
    assert again['site']['slug'] == slug
    assert again['site']['visibility'] == 'public'

    # get_artifact_publication 反查到该 Site
    pub = await studio_service.get_artifact_publication(session, subject=a, artifact_id=art.id)
    assert pub is not None and pub['slug'] == slug


async def test_publish_artifact_requires_manager(session: AsyncSession) -> None:
    """viewer 协作者不能发布成片（publish 走 manager 闸）。"""
    tag = uuid.uuid4().hex[:8]
    a = Subject.human(f'h_a_{tag}')
    b = Subject.human(f'h_b_{tag}')
    asset = await _make_public_video_asset(session, a.hasn_id)
    art = await _make_artifact(session, a.hasn_id, video_asset_uri=f'hasn://asset/{asset.asset_id}')
    await studio_service.add_artifact_share(
        session, subject=a, artifact_id=art.id, grantee_type='human', grantee_id=b.hasn_id, permission='viewer'
    )
    with pytest.raises(errors.ForbiddenError):
        await studio_service.publish_artifact(session, subject=b, artifact_id=art.id)


async def test_published_video_landing_renders_signed_url(session: AsyncSession) -> None:
    """/s/{slug} 渲染 video kind：用 revision.asset_id 现解析签名 URL 生成 <video> 落地（serve 边界）。

    这里直接驱动 publish_service.get_current_revision + 资产解析 + 落地页渲染纯函数（等价 hosting.content 的
    video-landing 分支），验证：① video kind 经 video-landing runtime 渲染；② URL 现解析进 <video src>。
    """
    from backend.app.hasn.service.hasn_asset_service import hasn_asset_service

    tag = uuid.uuid4().hex[:8]
    a = Subject.human(f'h_a_{tag}')
    asset = await _make_public_video_asset(session, a.hasn_id)
    art = await _make_artifact(session, a.hasn_id, video_asset_uri=f'hasn://asset/{asset.asset_id}')
    result = await studio_service.publish_artifact(session, subject=a, artifact_id=art.id, visibility='public')
    site_id = result['site']['id']

    revision = await publish_service.get_current_revision(session, site_id=site_id)
    assert revision is not None and revision.runtime == 'video-landing'
    resolved = await hasn_asset_service.resolve(session, requester_hasn_id=a.hasn_id, asset_ids=[revision.asset_id])
    video_url = next((r.display_url for r in resolved if r.asset_id == revision.asset_id), None)
    assert video_url, '应能现解析出成片签名/直读 URL'

    landing = _video_landing_html(result['site']['title'], video_url, allow_download=False)
    assert '<video' in landing and video_url in landing  # URL 现解析进落地页
    assert 'nodownload' in landing  # allow_download=False → 禁下载
    # CSP media-src 放行成片 host（否则 <video> 拉不到流）
    csp = _content_csp_for_video('https://share.example.com', video_url)
    assert 'media-src' in csp
