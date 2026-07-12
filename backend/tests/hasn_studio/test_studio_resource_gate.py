"""G6 统一资源权限门·studio 接入真实 PG 守卫测试（doc33 S3-1·零 mock）。

覆盖「门这条路」——`enforce_declaration` 经 studio adapter（studio_project / studio_artifact）判权，把已判权
资源经返回值（handler 侧再经 ContextVar）送达。与 `test_studio_resource_share.py`（直测 studio_service ACL
`authorize_project`/`authorize_artifact` 单一实现）互补：本文件锁死**平台门代劳判权**（分身经 manifest 工具面
声明的 `resource_access`，见 `hasn_studio/manifest.py`）的正确性——owner_grant=manager、显式 share 档位、
editor 档位不足 403、撤销/未共享/畸形 id → 404（存在性隐藏）、可选参缺省跳过（新建 save 路径）。

不调用真实 handler，只驱动 `enforce_declaration` + 经 studio_service 建行/建 share → flush（不 commit）→
断言 → rollback。共享名单复用平台 `hasn_resource_share`（studio_service.add_*_share 写入 resource_type
= 'studio_project'/'studio_artifact'），门经 `resolve_effective_permission` 内核读之（语义不动）。
studio 项目/成品无 visibility/scope/enterprise 列 → 纯显式 ACL（owner_grant + explicit_grant）。
"""

from __future__ import annotations

import uuid

from typing import TYPE_CHECKING

import pytest
import pytest_asyncio

from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from backend.app.hasn.service.authz.resource_gate import enforce_declaration
from backend.app.hasn_studio.model import StudioArtifact, StudioProject
from backend.app.hasn_studio.service import (
    resource_adapter as _studio_resource_adapter,  # noqa: F401  # import 即注册 adapter
)
from backend.app.hasn_studio.service.studio_service import Subject, studio_service
from backend.app.mcp.context import clear_authorized_resources, get_authorized_resource, set_authorized_resources
from backend.common.exception import errors
from backend.database.db import SQLALCHEMY_DATABASE_URL

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

    from sqlalchemy.ext.asyncio import AsyncSession

pytestmark = pytest.mark.asyncio

# 与 hasn_studio/manifest.py 一致的声明（内联以锁死门的判定契约）：
# 读类判 viewer、写/渲染类判 editor；type 逐字对齐 resource_share 存的 resource_type + adapter 注册类型。
_RA_PROJECT_VIEWER = [{'param': 'project_id', 'type': 'studio_project', 'need': 'viewer'}]
_RA_PROJECT_EDITOR = [{'param': 'project_id', 'type': 'studio_project', 'need': 'editor'}]
_RA_PROJECT_EDITOR_OPT = [{'param': 'project_id', 'type': 'studio_project', 'need': 'editor', 'required': False}]
_RA_ARTIFACT_VIEWER = [{'param': 'artifact_id', 'type': 'studio_artifact', 'need': 'viewer'}]


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


async def _make_artifact(session: AsyncSession, owner_hasn_id: str, *, title: str = '成片') -> StudioArtifact:
    row = StudioArtifact(
        project_id=0,
        owner_hasn_id=owner_hasn_id,
        title=title,
        pipeline_key='cinematic',
        video_asset_uri='',
        resolution='1080x1920',
        status='completed',
        origin_type='app',
    )
    session.add(row)
    await session.flush()
    return row


async def test_gate_owner_agent_gets_manager_attributed_to_owner(session: AsyncSession) -> None:
    """场景①：owner A 的分身过门（owner_grant）→ manager >= viewer/editor 成功，委托 owner key = A。
    项目与成品两类资源都覆盖；并验证 ContextVar 送达 handler 侧取到的 owner 就是 A。"""
    tag = uuid.uuid4().hex[:8]
    a = Subject.human(f'h_a_{tag}')
    a_agent = Subject.agent(f'a_a_{tag}', a.hasn_id)
    project = await _make_project(session, a.hasn_id)
    art = await _make_artifact(session, a.hasn_id)

    # 项目 viewer（读）与 editor（写/派渲染）均过门，档位 manager，委托键 = A
    v = await enforce_declaration(session, a_agent, _RA_PROJECT_VIEWER, {'project_id': project.id})
    assert v['project_id'].owner_hasn_id == a.hasn_id
    assert v['project_id'].permission == 'manager'
    e = await enforce_declaration(session, a_agent, _RA_PROJECT_EDITOR, {'project_id': project.id})
    assert e['project_id'].owner_hasn_id == a.hasn_id and e['project_id'].permission == 'manager'
    # 成品 viewer（导出）同样 manager
    av = await enforce_declaration(session, a_agent, _RA_ARTIFACT_VIEWER, {'artifact_id': art.id})
    assert av['artifact_id'].owner_hasn_id == a.hasn_id and av['artifact_id'].permission == 'manager'

    # ContextVar 送达：handler 侧取到的 owner 就是 A（落库归属正确，非分身主人 id 冒充）
    try:
        set_authorized_resources(v)
        got = get_authorized_resource('project_id')
        assert got is not None and got.owner_hasn_id == a.hasn_id
    finally:
        clear_authorized_resources()


async def test_gate_shared_viewer_reads_ok_writes_forbidden(session: AsyncSession) -> None:
    """场景②：A 共享项目 viewer 给 B → B 的分身可读（viewer 过门）不可写（editor 403）。"""
    tag = uuid.uuid4().hex[:8]
    a = Subject.human(f'h_a_{tag}')
    b = Subject.human(f'h_b_{tag}')
    b_agent = Subject.agent(f'a_b_{tag}', b.hasn_id)
    project = await _make_project(session, a.hasn_id)
    await studio_service.add_project_share(
        session, subject=a, project_id=project.id, grantee_type='human', grantee_id=b.hasn_id, permission='viewer'
    )

    ok = await enforce_declaration(session, b_agent, _RA_PROJECT_VIEWER, {'project_id': project.id})
    assert ok['project_id'].owner_hasn_id == a.hasn_id and ok['project_id'].permission == 'viewer'
    # editor 档位不足 → 403（有权但档位不足，非 404）
    with pytest.raises(errors.ForbiddenError):
        await enforce_declaration(session, b_agent, _RA_PROJECT_EDITOR, {'project_id': project.id})


async def test_gate_shared_editor_can_write(session: AsyncSession) -> None:
    """场景③：A 共享项目 editor 给别人的分身 → 该分身 editor 过门（协作分身改项目/派渲染）。"""
    tag = uuid.uuid4().hex[:8]
    a = Subject.human(f'h_a_{tag}')
    collab = Subject.agent(f'a_collab_{tag}', owner_hasn_id=f'h_other_{tag}')  # 别人的分身
    project = await _make_project(session, a.hasn_id)
    await studio_service.add_project_share(
        session, subject=a, project_id=project.id, grantee_type='agent', grantee_id=collab.hasn_id, permission='editor'
    )

    e = await enforce_declaration(session, collab, _RA_PROJECT_EDITOR, {'project_id': project.id})
    assert e['project_id'].owner_hasn_id == a.hasn_id and e['project_id'].permission == 'editor'


async def test_gate_artifact_shared_viewer_reads(session: AsyncSession) -> None:
    """场景④：A 共享成品 viewer 给 B → B 的分身 viewer 过门（导出该成品）；未共享成品 → 404。"""
    tag = uuid.uuid4().hex[:8]
    a = Subject.human(f'h_a_{tag}')
    b = Subject.human(f'h_b_{tag}')
    b_agent = Subject.agent(f'a_b_{tag}', b.hasn_id)
    art = await _make_artifact(session, a.hasn_id, title='A 的成片')
    other = await _make_artifact(session, a.hasn_id, title='A 未共享的成片')
    await studio_service.add_artifact_share(
        session, subject=a, artifact_id=art.id, grantee_type='human', grantee_id=b.hasn_id, permission='viewer'
    )

    ok = await enforce_declaration(session, b_agent, _RA_ARTIFACT_VIEWER, {'artifact_id': art.id})
    assert ok['artifact_id'].owner_hasn_id == a.hasn_id and ok['artifact_id'].permission == 'viewer'
    # 未共享的成品 → 404（存在性隐藏）
    with pytest.raises(errors.NotFoundError):
        await enforce_declaration(session, b_agent, _RA_ARTIFACT_VIEWER, {'artifact_id': other.id})


async def test_gate_revoke_and_never_shared_and_malformed_are_not_found(session: AsyncSession) -> None:
    """场景⑤：撤销/从未共享私有项目 → 404（存在性隐藏）；畸形/不存在 id → 404（不冒 500）。"""
    tag = uuid.uuid4().hex[:8]
    a = Subject.human(f'h_a_{tag}')
    b = Subject.human(f'h_b_{tag}')
    b_agent = Subject.agent(f'a_b_{tag}', b.hasn_id)
    project = await _make_project(session, a.hasn_id)
    never = await _make_project(session, a.hasn_id, title='从未共享')

    await studio_service.add_project_share(
        session, subject=a, project_id=project.id, grantee_type='human', grantee_id=b.hasn_id, permission='editor'
    )
    # 撤销前能读
    await enforce_declaration(session, b_agent, _RA_PROJECT_VIEWER, {'project_id': project.id})
    await studio_service.revoke_project_share(
        session, subject=a, project_id=project.id, grantee_type='human', grantee_id=b.hasn_id
    )
    # 撤销后 / 从未共享 → 404
    with pytest.raises(errors.NotFoundError):
        await enforce_declaration(session, b_agent, _RA_PROJECT_VIEWER, {'project_id': project.id})
    with pytest.raises(errors.NotFoundError):
        await enforce_declaration(session, b_agent, _RA_PROJECT_VIEWER, {'project_id': never.id})
    # 畸形 id / 不存在 id → 404（不冒 500），项目与成品两类适配器都验
    with pytest.raises(errors.NotFoundError):
        await enforce_declaration(session, b_agent, _RA_PROJECT_VIEWER, {'project_id': 'not-an-int'})
    with pytest.raises(errors.NotFoundError):
        await enforce_declaration(session, b_agent, _RA_PROJECT_VIEWER, {'project_id': 999_000_111})
    with pytest.raises(errors.NotFoundError):
        await enforce_declaration(session, b_agent, _RA_ARTIFACT_VIEWER, {'artifact_id': 'not-an-int'})


async def test_gate_optional_param_absent_skips_new_create(session: AsyncSession) -> None:
    """声明入参语义：save_project 的 project_id 可选（required=False）——新建（缺省）→ 跳过判权，
    不炸 422（对齐 save_project project_id=None 新建路径）。"""
    tag = uuid.uuid4().hex[:8]
    a_agent = Subject.agent(f'a_a_{tag}', f'h_a_{tag}')
    out = await enforce_declaration(session, a_agent, _RA_PROJECT_EDITOR_OPT, {})  # 无 project_id
    assert out == {}  # 缺省 → 无判权项
