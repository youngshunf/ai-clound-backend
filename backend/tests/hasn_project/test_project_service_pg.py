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

from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
import sqlalchemy as sa

from backend.app.hasn.model.hasn_agents import HasnAgents
from backend.app.hasn.model.hasn_artifact_contributions import HasnArtifactContributions
from backend.app.hasn.model.hasn_artifacts import HasnArtifacts
from backend.app.hasn.model.hasn_assets import HasnAssets
from backend.app.hasn.model.hasn_sessions import HasnSessions
from backend.app.hasn_designsystem.model.design_system import DesignSystem
from backend.app.hasn_designsystem.service import project_linkage as _designsystem_project_linkage  # noqa: F401
from backend.app.mcp.auth import AgentContext
from backend.app.hasn_project.model.hasn_project_milestone import HasnProjectMilestone
from backend.app.mcp.tools.project import _h_update
from backend.app.hasn_project.service.project_app_service import ProjectService
from backend.app.hasn_project.service.project_linkage_registry import project_linkage_registry
from backend.common.exception import errors
from backend.database.db import async_db_session
from backend.utils.timezone import timezone

pytestmark = pytest.mark.asyncio(loop_scope='session')


def _owner() -> str:
    return f'hasnOwner_{uuid4().hex[:18]}'


async def _seed_artifact(db, *, owner: str, agent: str, artifact_id: str) -> None:
    """插一条 owner 名下的产物行（link/unlink/并集读用）。只填 NOT NULL 关键列，其余走 DB 默认。"""
    db.add(
        HasnArtifacts(
            artifact_id=artifact_id,
            agent_hasn_id=agent,
            owner_hasn_id=owner,
            artifact_key=f'test:{artifact_id}',
            artifact_kind='resource',
            kind='resource',
            resource_uri=f'hasn://artifact/{artifact_id}',
            source_kind='app',
            status='active',
            title=f'产物 {artifact_id}',
        )
    )
    await db.flush()


async def _seed_contribution(
    db,
    *,
    owner: str,
    agent: str,
    artifact_id: str,
    project_id: str | None,
) -> None:
    """插入一条不可变参与记录，覆盖项目流的历史参与关系。"""
    db.add(
        HasnArtifactContributions(
            contribution_id=f'con_{uuid4().hex[:20]}',
            artifact_id=artifact_id,
            owner_hasn_id=owner,
            agent_hasn_id=agent,
            project_id=project_id,
            action='create',
            source_kind='app_write',
            idempotency_key=f'test:{uuid4().hex}',
        )
    )
    await db.flush()


async def _seed_owned_asset(db, *, owner: str, asset_id: str) -> None:
    """插入一个当前主人真实拥有的封面资产。"""
    db.add(
        HasnAssets(
            asset_id=asset_id,
            owner_hasn_id=owner,
            access='private',
            storage_id=1,
            object_key=f'project-test/{asset_id}.png',
            kind='image',
            mime='image/png',
            size_bytes=1,
            extract_status='done',
        )
    )
    await db.flush()


async def _seed_owned_agent(db, *, owner: str, agent_id: str) -> None:
    """插入一个当前主人真实拥有的活跃分身。"""
    db.add(
        HasnAgents(
            hasn_id=agent_id,
            star_id=f'{agent_id[:20]}#star',
            owner_id=owner,
            display_name='项目协作分身',
            agent_name='project-collaborator',
            type='cloud',
            role='specialist',
            api_key_hash='test-key',
            status='active',
            created_via='client',
        )
    )
    await db.flush()


def _assert_request_error(exc: pytest.ExceptionInfo[errors.RequestError], *, code: int, error_code: str) -> None:
    """统一断言项目字段校验的 HTTP 状态与机器错误码。"""
    assert exc.value.code == code
    assert exc.value.data == {'error_code': error_code}


async def test_artifact_orm_defaults_satisfy_current_state_constraints() -> None:
    """当前态产物未显式给类型时，ORM 默认值也必须满足真实 PostgreSQL 约束。"""
    artifact_id = f'art_{uuid4().hex[:20]}'
    async with async_db_session() as db:
        try:
            artifact = HasnArtifacts(
                artifact_id=artifact_id,
                agent_hasn_id=f'a_{uuid4().hex[:12]}',
                owner_hasn_id=_owner(),
                artifact_key=f'resource:hasn://project/{uuid4()}',
                resource_uri=f'hasn://project/{uuid4()}',
            )
            db.add(artifact)
            await db.flush()
            assert artifact.artifact_kind == 'resource'
            assert artifact.status == 'active'
        finally:
            await db.rollback()


async def test_project_patch_preserves_omitted_fields_and_clears_explicit_nulls() -> None:
    """项目 PATCH 必须区分未传与显式 null，并校验封面和默认分身确属主人。"""
    owner = _owner()
    asset_id = f'ast_{uuid4().hex[:16]}'
    agent_id = f'a_{uuid4().hex[:16]}'
    svc = ProjectService()
    async with async_db_session() as db:
        try:
            await _seed_owned_asset(db, owner=owner, asset_id=asset_id)
            await _seed_owned_agent(db, owner=owner, agent_id=agent_id)
            project = await svc.create_project(
                db,
                owner=owner,
                data={
                    'name': '字段语义项目',
                    'goal': '保留目标',
                    'cover_asset_uri': f'hasn://asset/{asset_id}',
                    'bound_agent_id': agent_id,
                },
            )

            renamed = await svc.update_project(db, owner=owner, pk=project['id'], data={'name': '改名'})
            assert renamed['goal'] == '保留目标'
            assert renamed['cover_asset_uri'] == f'hasn://asset/{asset_id}'
            assert renamed['bound_agent_id'] == agent_id

            cleared = await svc.update_project(
                db,
                owner=owner,
                pk=project['id'],
                data={'goal': None, 'cover_asset_uri': None, 'bound_agent_id': None},
            )
            assert cleared['goal'] is None
            assert cleared['cover_asset_uri'] is None
            assert cleared['bound_agent_id'] is None

            normalized = await svc.update_project(db, owner=owner, pk=project['id'], data={'goal': '   '})
            assert normalized['goal'] is None
            with pytest.raises(errors.RequestError) as exc:
                await svc.update_project(db, owner=owner, pk=project['id'], data={'name': None})
            _assert_request_error(exc, code=400, error_code='INVALID_PROJECT_NAME')
        finally:
            await db.rollback()


async def test_project_rejects_invalid_or_foreign_cover_and_bound_agent() -> None:
    """封面和默认分身必须是当前主人的真实资源，URI 不得夹带路径或外部 URL。"""
    owner, other = _owner(), _owner()
    other_asset_id = f'ast_{uuid4().hex[:16]}'
    other_agent_id = f'a_{uuid4().hex[:16]}'
    svc = ProjectService()
    async with async_db_session() as db:
        try:
            await _seed_owned_asset(db, owner=other, asset_id=other_asset_id)
            await _seed_owned_agent(db, owner=other, agent_id=other_agent_id)
            with pytest.raises(errors.RequestError) as malformed:
                await svc.create_project(
                    db,
                    owner=owner,
                    data={'name': '非法封面', 'cover_asset_uri': 'hasn://asset/ast_x/extra'},
                )
            _assert_request_error(malformed, code=422, error_code='INVALID_COVER_ASSET')
            with pytest.raises(errors.RequestError) as foreign_asset:
                await svc.create_project(
                    db,
                    owner=owner,
                    data={'name': '他人封面', 'cover_asset_uri': f'hasn://asset/{other_asset_id}'},
                )
            _assert_request_error(foreign_asset, code=422, error_code='INVALID_COVER_ASSET')
            with pytest.raises(errors.RequestError) as foreign_agent:
                await svc.create_project(
                    db,
                    owner=owner,
                    data={'name': '他人分身', 'bound_agent_id': other_agent_id},
                )
            _assert_request_error(foreign_agent, code=422, error_code='INVALID_BOUND_AGENT')
        finally:
            await db.rollback()


async def test_archived_project_rejects_new_writes_but_can_be_restored() -> None:
    """归档项目可读可恢复，但不能再修改业务字段或新增里程碑。"""
    owner = _owner()
    svc = ProjectService()
    async with async_db_session() as db:
        try:
            project = await svc.create_project(db, owner=owner, data={'name': '归档项目'})
            await svc.archive_project(db, owner=owner, pk=project['id'])
            assert (await svc.get_project(db, owner=owner, pk=project['id']))['status'] == 'archived'
            with pytest.raises(errors.RequestError) as patch_exc:
                await svc.update_project(db, owner=owner, pk=project['id'], data={'goal': '不允许写'})
            _assert_request_error(patch_exc, code=409, error_code='PROJECT_ARCHIVED')
            with pytest.raises(errors.RequestError) as milestone_exc:
                await svc.create_milestone(db, owner=owner, project_id=project['id'], data={'name': '不允许新增'})
            _assert_request_error(milestone_exc, code=409, error_code='PROJECT_ARCHIVED')
            restored = await svc.update_project(db, owner=owner, pk=project['id'], data={'status': 'active'})
            assert restored['status'] == 'active'
        finally:
            await db.rollback()


async def test_project_mcp_update_uses_same_cover_validation_as_owner_service() -> None:
    """MCP 工具必须复用项目 service 的 422 封面校验，不能保留另一套 400 规则。"""
    owner = _owner()
    svc = ProjectService()
    context = AgentContext(
        hasn_id=f'a_{uuid4().hex[:16]}',
        owner_id=1,
        agent_status='active',
        metadata={},
        owner_hasn_id=owner,
        session_uuid=str(uuid4()),
    )
    async with async_db_session() as db:
        try:
            project = await svc.create_project(db, owner=owner, data={'name': '工具校验项目'})
            with pytest.raises(errors.RequestError) as exc:
                await _h_update(
                    db,
                    context,
                    {'id': project['id'], 'cover_asset_uri': 'https://invalid.example/cover.png'},
                )
            _assert_request_error(exc, code=422, error_code='INVALID_COVER_ASSET')
        finally:
            await db.rollback()


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


async def test_project_summary_is_set_based_complete_and_keeps_zero_values() -> None:
    """列表与详情摘要必须覆盖真实聚合，空项目的所有计数也必须显式为零。"""
    owner = _owner()
    bound_agent = f'a_{uuid4().hex[:12]}'
    participation_agent = f'a_{uuid4().hex[:12]}'
    session_agents = [f'a_{uuid4().hex[:12]}' for _ in range(3)]
    svc = ProjectService()
    base_time = datetime(2026, 7, 26, 8, 0, tzinfo=UTC)

    async with async_db_session() as db:
        try:
            await _seed_owned_agent(db, owner=owner, agent_id=bound_agent)
            active = await svc.create_project(
                db,
                owner=owner,
                data={'name': '聚合项目', 'bound_agent_id': bound_agent},
            )
            archived = await svc.create_project(db, owner=owner, data={'name': '空归档项目'})
            await svc.archive_project(db, owner=owner, pk=archived['id'])

            active_id = active['id']
            direct_artifact = f'art_{uuid4().hex[:16]}'
            participation_artifact = f'art_{uuid4().hex[:16]}'
            container_artifact = f'art_{uuid4().hex[:16]}'
            await _seed_artifact(db, owner=owner, agent=bound_agent, artifact_id=direct_artifact)
            await _seed_artifact(db, owner=owner, agent=participation_agent, artifact_id=participation_artifact)
            await _seed_artifact(db, owner=owner, agent=bound_agent, artifact_id=container_artifact)
            await db.execute(
                sa.update(HasnArtifacts)
                .where(HasnArtifacts.artifact_id == direct_artifact)
                .values(project_id=active_id, updated_time=base_time + timedelta(minutes=2))
            )
            await _seed_contribution(
                db,
                owner=owner,
                agent=participation_agent,
                artifact_id=participation_artifact,
                project_id=active_id,
            )
            await db.execute(
                sa.update(HasnArtifactContributions)
                .where(HasnArtifactContributions.artifact_id == participation_artifact)
                .values(occurred_time=base_time + timedelta(minutes=3))
            )

            db.add_all(
                [
                    HasnProjectMilestone(project_id=active_id, name='已完成', status='done'),
                    HasnProjectMilestone(project_id=active_id, name='待完成', status='pending'),
                    HasnSessions(
                        session_id=f'sess_{uuid4().hex[:16]}',
                        owner_id=owner,
                        hasn_id=session_agents[0],
                        session_kind='task',
                        session_scope='summary_only',
                        session_status='active',
                        origin_type='app',
                        project_id=active_id,
                        last_message_at=base_time + timedelta(minutes=4),
                    ),
                    HasnSessions(
                        session_id=f'sess_{uuid4().hex[:16]}',
                        owner_id=owner,
                        hasn_id=session_agents[1],
                        session_kind='task',
                        session_scope='summary_only',
                        session_status='waiting_for_user',
                        origin_type='app',
                        project_id=active_id,
                    ),
                    HasnSessions(
                        session_id=f'sess_{uuid4().hex[:16]}',
                        owner_id=owner,
                        hasn_id=session_agents[2],
                        session_kind='task',
                        session_scope='summary_only',
                        session_status='completed',
                        origin_type='app',
                        project_id=active_id,
                    ),
                ]
            )
            await db.flush()
            await db.execute(
                sa.update(HasnSessions)
                .where(HasnSessions.owner_id == owner, HasnSessions.project_id == active_id)
                .values(created_time=base_time, updated_time=base_time)
            )
            await db.execute(
                sa.update(HasnProjectMilestone)
                .where(HasnProjectMilestone.project_id == active_id)
                .values(updated_time=base_time + timedelta(minutes=1))
            )

            design_system = DesignSystem(
                owner_hasn_id=owner,
                name='项目设计系统',
                slug=f'project-summary-{uuid4().hex[:12]}',
                platform_project_id=active_id,
            )
            db.add(design_system)
            await db.flush()
            await db.execute(
                sa.update(HasnArtifacts)
                .where(HasnArtifacts.artifact_id == container_artifact)
                .values(
                    resource_uri=f'hasn://designsystem/{design_system.id}',
                    created_time=base_time,
                    updated_time=base_time + timedelta(minutes=2),
                )
            )
            await db.execute(
                sa.update(DesignSystem)
                .where(DesignSystem.id == design_system.id)
                .values(updated_time=base_time + timedelta(minutes=5))
            )
            await db.flush()

            rows = await svc.list_projects(db, owner=owner)
            assert [row['id'] for row in rows] == [active_id, archived['id']]
            summary = rows[0]
            assert summary['artifact_count'] == 3
            assert summary['session_count'] == 3
            assert summary['active_session_count'] == 2
            assert summary['milestone_done_count'] == 1
            assert summary['milestone_total_count'] == 2
            assert summary['link_count'] == 1
            assert summary['linked_apps'] == [{'app_id': 'designsystem', 'count': 1}]
            assert summary['agent_ids'] == [bound_agent, *sorted({participation_agent, *session_agents})]
            assert summary['agent_count'] == 5
            expected_activity = (
                await db.execute(
                    sa.select(DesignSystem.updated_time).where(DesignSystem.id == design_system.id)
                )
            ).scalar_one()
            assert expected_activity is not None
            assert summary['last_activity_time'] == timezone.to_str(timezone.from_datetime(expected_activity))

            empty = rows[1]
            assert {
                key: empty[key]
                for key in (
                    'artifact_count',
                    'session_count',
                    'active_session_count',
                    'agent_count',
                    'link_count',
                    'milestone_done_count',
                    'milestone_total_count',
                )
            } == {
                'artifact_count': 0,
                'session_count': 0,
                'active_session_count': 0,
                'agent_count': 0,
                'link_count': 0,
                'milestone_done_count': 0,
                'milestone_total_count': 0,
            }
            assert empty['agent_ids'] == []
            assert empty['linked_apps'] == []

            detail = await svc.get_project(db, owner=owner, pk=active_id)
            assert detail['summary'] == summary
            assert len(detail['recent_sessions']) == 3
            assert {session['status'] for session in detail['recent_sessions']} == {
                'running',
                'waiting',
                'completed',
            }
            assert 'designsystem' in detail['linkable_domains']
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
            await _seed_contribution(
                db,
                owner=owner,
                agent=agent,
                artifact_id=art_in,
                project_id=None,
            )
            # 只把 art_in 挂进项目。
            await project_linkage_registry.link(
                db, owner=owner, resource_uri=f'hasn://artifact/{art_in}', project_id=proj['id']
            )
            flow = await svc.project_artifact_flow(db, owner=owner, project_id=proj['id'])
            ids = {r['artifact_id'] for r in flow['items']}
            assert art_in in ids
            assert art_out not in ids  # 未挂靠的不进流
            assert flow['total'] == 1
            assert next(r for r in flow['items'] if r['artifact_id'] == art_in)['project_relation'] == {
                'project_id': proj['id'],
                'via': 'explicit_resource_link',
            }
        finally:
            await db.rollback()


async def test_project_artifact_flow_unifies_history_explicit_link_and_pagination() -> None:
    """项目流按权威三路并集查询，历史参与不被当前项目挂靠覆盖。"""
    owner = _owner()
    agent = f'a_{uuid4().hex[:12]}'
    historical_artifact = f'art_{uuid4().hex[:16]}'
    explicit_artifact = f'art_{uuid4().hex[:16]}'
    svc = ProjectService()
    async with async_db_session() as db:
        try:
            project_a = await svc.create_project(db, owner=owner, data={'name': '历史项目 A'})
            project_b = await svc.create_project(db, owner=owner, data={'name': '历史项目 B'})
            await _seed_artifact(db, owner=owner, agent=agent, artifact_id=historical_artifact)
            await _seed_artifact(db, owner=owner, agent=agent, artifact_id=explicit_artifact)
            await _seed_contribution(
                db,
                owner=owner,
                agent=agent,
                artifact_id=historical_artifact,
                project_id=project_a['id'],
            )
            await _seed_contribution(
                db,
                owner=owner,
                agent=agent,
                artifact_id=historical_artifact,
                project_id=project_b['id'],
            )
            await _seed_contribution(
                db,
                owner=owner,
                agent=agent,
                artifact_id=explicit_artifact,
                project_id=None,
            )
            await project_linkage_registry.link(
                db,
                owner=owner,
                resource_uri=f'hasn://artifact/{explicit_artifact}',
                project_id=project_a['id'],
            )

            first_page = await svc.project_artifact_flow(
                db,
                owner=owner,
                project_id=project_a['id'],
                page=1,
                size=1,
            )
            second_page = await svc.project_artifact_flow(
                db,
                owner=owner,
                project_id=project_a['id'],
                page=2,
                size=1,
            )
            project_b_flow = await svc.project_artifact_flow(
                db,
                owner=owner,
                project_id=project_b['id'],
            )

            assert first_page['total'] == 2
            assert first_page['page'] == 1
            assert first_page['size'] == 1
            assert len(first_page['items']) == 1
            assert second_page['total'] == 2
            assert second_page['page'] == 2
            assert len(second_page['items']) == 1
            assert {row['artifact_id'] for row in first_page['items'] + second_page['items']} == {
                historical_artifact,
                explicit_artifact,
            }

            rows_a = {
                row['artifact_id']: row for row in first_page['items'] + second_page['items']
            }
            assert rows_a[historical_artifact]['project_relation'] == {
                'project_id': project_a['id'],
                'via': 'participation',
            }
            assert rows_a[explicit_artifact]['project_relation'] == {
                'project_id': project_a['id'],
                'via': 'explicit_resource_link',
            }
            assert project_b_flow['total'] == 1
            assert project_b_flow['items'][0]['artifact_id'] == historical_artifact
            assert project_b_flow['items'][0]['project_relation'] == {
                'project_id': project_b['id'],
                'via': 'participation',
            }
        finally:
            await db.rollback()
