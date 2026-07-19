"""平台项目（doc38 PJ U3）owner 隔离 service + 挂靠点注册表 + 产物流并集读 真实 PG 验证。

零 mock：真实本地 PostgreSQL(15432) 跑 ProjectService + ProjectLinkageRegistry；事务回滚不污染库。
需要：本地 PG 起在 15432、schema `hasn_project` 已建（U1 迁移）。

覆盖（doc38 §3/§4/§5）：
- **项目 CRUD**：建/查（含里程碑轨）/改/归档，name 必填、status 白名单、owner 隔离（跨 owner 404）；
- **里程碑**：建/改/完成（纯业务态 pending↔done，无门控），经父项目校验 owner；
- **挂靠点注册表**：`link`/`unlink` 经 artifact adapter 落 `hasn_artifacts.project_id`（唯一收口，不散写），
  跨 owner 资源挂不进（404）、非法域拒；
- **产物流并集读**：`project_id` 直接命中（register-on-write 自动打标 / 显式 link）汇入
  `project_artifact_flow`；容器分支由各应用 adapter 的独立契约覆盖。
"""

from __future__ import annotations

from uuid import uuid4

import pytest
import sqlalchemy as sa

from backend.app.hasn.model.hasn_artifacts import HasnArtifacts
from backend.app.hasn_project.service.project_app_service import ProjectService
from backend.app.hasn_project.service.project_linkage_registry import project_linkage_registry
from backend.common.exception import errors
from backend.database.db import async_db_session

pytestmark = pytest.mark.asyncio(loop_scope='module')


def _owner() -> str:
    return f'hasnOwner_{uuid4().hex[:18]}'


async def _seed_artifact(db, *, owner: str, agent: str, artifact_id: str) -> None:
    """插一条 owner 名下的产物行（link/unlink/并集读用）。只填 NOT NULL 关键列，其余走 DB 默认。"""
    db.add(
        HasnArtifacts(
            artifact_id=artifact_id,
            agent_hasn_id=agent,
            owner_hasn_id=owner,
            kind='resource',
            source_kind='app',
            status='active',
            title=f'产物 {artifact_id}',
        )
    )
    await db.flush()


# ── 项目 CRUD ────────────────────────────────────────────────────────────────
async def test_create_get_update_archive_roundtrip() -> None:
    """建→查→改→归档全链路：字段落库、里程碑轨随查出、归档只改状态不删。"""
    owner = _owner()
    svc = ProjectService()
    async with async_db_session() as db:
        try:
            proj = await svc.create_project(db, owner=owner, data={'name': '换根重构', 'goal': '把项目当第三条轴'})
            assert proj['name'] == '换根重构'
            assert proj['status'] == 'active'  # 默认活跃
            assert proj['owner_id'] == owner

            got = await svc.get_project(db, owner=owner, pk=proj['id'])
            assert got['id'] == proj['id']
            assert got['milestones'] == []  # 新建无里程碑，轨为空

            upd = await svc.update_project(db, owner=owner, pk=proj['id'], data={'goal': '改后的目标'})
            assert upd['goal'] == '改后的目标'
            assert upd['name'] == '换根重构'  # 未传的字段不动

            arch = await svc.archive_project(db, owner=owner, pk=proj['id'])
            assert arch['status'] == 'archived'
        finally:
            await db.rollback()


async def test_create_project_name_required() -> None:
    """name 为空 → 业务 400（name_required），不落库。"""
    owner = _owner()
    svc = ProjectService()
    async with async_db_session() as db:
        try:
            with pytest.raises(errors.RequestError):
                await svc.create_project(db, owner=owner, data={'name': '   '})
        finally:
            await db.rollback()


async def test_update_project_invalid_status_rejected() -> None:
    """status 非白名单（仅 active/archived）→ 业务 400。"""
    owner = _owner()
    svc = ProjectService()
    async with async_db_session() as db:
        try:
            proj = await svc.create_project(db, owner=owner, data={'name': 'P'})
            with pytest.raises(errors.RequestError):
                await svc.update_project(db, owner=owner, pk=proj['id'], data={'status': 'deleted'})
        finally:
            await db.rollback()


async def test_list_orders_active_before_archived() -> None:
    """列表 active 在前、archived 在后；只列本人项目。"""
    owner = _owner()
    svc = ProjectService()
    async with async_db_session() as db:
        try:
            a = await svc.create_project(db, owner=owner, data={'name': '活跃A'})
            b = await svc.create_project(db, owner=owner, data={'name': '归档B'})
            await svc.archive_project(db, owner=owner, pk=b['id'])
            rows = await svc.list_projects(db, owner=owner)
            assert [r['id'] for r in rows] == [a['id'], b['id']]  # active 在前
            assert all(r['owner_id'] == owner for r in rows)
        finally:
            await db.rollback()


async def test_cross_owner_get_404() -> None:
    """他人项目对本 owner 不可见（不泄漏存在性）→ 404。"""
    owner_a, owner_b = _owner(), _owner()
    svc = ProjectService()
    async with async_db_session() as db:
        try:
            proj = await svc.create_project(db, owner=owner_a, data={'name': 'A 的项目'})
            with pytest.raises(errors.NotFoundError):
                await svc.get_project(db, owner=owner_b, pk=proj['id'])
        finally:
            await db.rollback()


# ── 里程碑（纯业务态·无门控） ─────────────────────────────────────────────────
async def test_milestone_create_update_complete() -> None:
    """里程碑建/改/完成：pending→done 纯状态标记，随项目查出。"""
    owner = _owner()
    svc = ProjectService()
    async with async_db_session() as db:
        try:
            proj = await svc.create_project(db, owner=owner, data={'name': 'P'})
            ms = await svc.create_milestone(db, owner=owner, project_id=proj['id'], data={'name': '里程碑1'})
            assert ms['status'] == 'pending'
            assert ms['project_id'] == proj['id']

            upd = await svc.update_milestone(db, owner=owner, milestone_id=int(ms['id']), data={'sort': 5})
            assert upd['sort'] == 5

            done = await svc.complete_milestone(db, owner=owner, milestone_id=int(ms['id']))
            assert done['status'] == 'done'

            detail = await svc.get_project(db, owner=owner, pk=proj['id'])
            assert len(detail['milestones']) == 1
            assert detail['milestones'][0]['status'] == 'done'
        finally:
            await db.rollback()


async def test_milestone_cross_owner_404() -> None:
    """他人项目的里程碑对本 owner 不可操作（经父项目归属校验）→ 404。"""
    owner_a, owner_b = _owner(), _owner()
    svc = ProjectService()
    async with async_db_session() as db:
        try:
            proj = await svc.create_project(db, owner=owner_a, data={'name': 'A 的项目'})
            ms = await svc.create_milestone(db, owner=owner_a, project_id=proj['id'], data={'name': 'M'})
            with pytest.raises(errors.NotFoundError):
                await svc.complete_milestone(db, owner=owner_b, milestone_id=int(ms['id']))
        finally:
            await db.rollback()


# ── 挂靠点注册表：link/unlink 经 artifact adapter ─────────────────────────────
async def test_link_unlink_artifact_via_registry() -> None:
    """产物经 `hasn://artifact/{id}` 显式挂进/摘出项目——注册表落 `hasn_artifacts.project_id`。"""
    owner = _owner()
    agent = f'a_{uuid4().hex[:12]}'
    art_id = f'art_{uuid4().hex[:16]}'
    svc = ProjectService()
    async with async_db_session() as db:
        try:
            proj = await svc.create_project(db, owner=owner, data={'name': 'P'})
            await _seed_artifact(db, owner=owner, agent=agent, artifact_id=art_id)

            # 挂靠前先经工具侧一样的归属校验（link 不写他人项目）。
            await svc.assert_owned(db, owner=owner, pk=proj['id'])
            r = await project_linkage_registry.link(
                db, owner=owner, resource_uri=f'hasn://artifact/{art_id}', project_id=proj['id']
            )
            assert r['linked'] is True
            pid = (
                await db.execute(sa.select(HasnArtifacts.project_id).where(HasnArtifacts.artifact_id == art_id))
            ).scalar_one()
            assert str(pid) == proj['id']  # 挂靠列已落项目 id

            r2 = await project_linkage_registry.unlink(db, owner=owner, resource_uri=f'hasn://artifact/{art_id}')
            assert r2['unlinked'] is True
            pid2 = (
                await db.execute(sa.select(HasnArtifacts.project_id).where(HasnArtifacts.artifact_id == art_id))
            ).scalar_one()
            assert pid2 is None  # 摘除后置 NULL
        finally:
            await db.rollback()


async def test_link_cross_owner_artifact_404() -> None:
    """挂靠他人产物 → owner 隔离兜死 404（adapter 定位强制 owner_column == owner）。"""
    owner_a, owner_b = _owner(), _owner()
    agent = f'a_{uuid4().hex[:12]}'
    art_id = f'art_{uuid4().hex[:16]}'
    svc = ProjectService()
    async with async_db_session() as db:
        try:
            proj_b = await svc.create_project(db, owner=owner_b, data={'name': 'B 的项目'})
            await _seed_artifact(db, owner=owner_a, agent=agent, artifact_id=art_id)  # 产物属 A
            with pytest.raises(errors.NotFoundError):
                await project_linkage_registry.link(
                    db, owner=owner_b, resource_uri=f'hasn://artifact/{art_id}', project_id=proj_b['id']
                )
        finally:
            await db.rollback()


async def test_link_unsupported_domain_rejected() -> None:
    """未注册的资源域挂靠 → 业务 400（unsupported_link_domain）。U3 只注册了 artifact。"""
    owner = _owner()
    svc = ProjectService()
    async with async_db_session() as db:
        try:
            proj = await svc.create_project(db, owner=owner, data={'name': 'P'})
            with pytest.raises(errors.RequestError):
                await project_linkage_registry.link(
                    db, owner=owner, resource_uri='hasn://deck/deck_x', project_id=proj['id']
                )
        finally:
            await db.rollback()


# ── 产物流并集读 ─────────────────────────────────────────────────────────────
async def test_project_artifact_flow_direct_hits() -> None:
    """`project_id` 直接命中的产物汇入并集读，未挂靠产物不混入。"""
    owner = _owner()
    agent = f'a_{uuid4().hex[:12]}'
    art_in, art_out = f'art_{uuid4().hex[:16]}', f'art_{uuid4().hex[:16]}'
    svc = ProjectService()
    async with async_db_session() as db:
        try:
            proj = await svc.create_project(db, owner=owner, data={'name': 'P'})
            await _seed_artifact(db, owner=owner, agent=agent, artifact_id=art_in)
            await _seed_artifact(db, owner=owner, agent=agent, artifact_id=art_out)
            # 只把 art_in 挂进项目。
            await project_linkage_registry.link(
                db, owner=owner, resource_uri=f'hasn://artifact/{art_in}', project_id=proj['id']
            )
            flow = await svc.project_artifact_flow(db, owner=owner, project_id=proj['id'])
            ids = {r['artifact_id'] for r in flow}
            assert art_in in ids
            assert art_out not in ids  # 未挂靠的不进流
            assert next(r for r in flow if r['artifact_id'] == art_in)['via'] == 'linked'
        finally:
            await db.rollback()
