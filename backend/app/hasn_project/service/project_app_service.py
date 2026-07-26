"""平台项目（app_id=project，模块 14 doc38）owner 隔离业务 service（云端权威实现）。

设计事实源：docs/hasn-node设计文档/14-AI-Native应用平台/38-项目管理一级应用(平台项目·联邦挂靠)设计.md §3/§4/§5。

与 plan/designsystem 同范式：codegen 生成的泛型 per-table service/api（keyed on int pk、引用 user_id）
留盘不接线，与本应用 owner_hasn_id/UUID 主键身份模型不兼容；真实业务面由本 service + `hasn.project.*`
云端平台工具 + 自定义 owner API 承载。

owner 隔离铁律（doc38 三铁律之外的隔离约束）：所有读写按 `owner_id`（主人 HASN ID）过滤，
**owner 身份永远由调用方（Owner JWT / Agent JWT claims / MCP Key）解析后传入 `owner=`，绝不接受
请求体携带的身份**。里程碑经父项目归属校验间接落到 owner。项目**不是权限边界**——这两条 scope 只
控「分身能否操作主人的项目容器」，跨 owner 由本 service 的 `owner_id` 过滤兜死。
"""

from __future__ import annotations

import re

from collections.abc import Sequence
from datetime import datetime
from typing import TYPE_CHECKING, Any
from uuid import UUID

import sqlalchemy as sa

from backend.app.hasn.model.hasn_agents import HasnAgents
from backend.app.hasn.model.hasn_artifact_contributions import HasnArtifactContributions
from backend.app.hasn.model.hasn_artifacts import HasnArtifacts
from backend.app.hasn.model.hasn_assets import HasnAssets
from backend.app.hasn.model.hasn_sessions import HasnSessions
from backend.app.hasn.service.artifact_query_service import artifact_query_service
from backend.app.hasn.service.hasn_sessions_service import hasn_sessions_service
from backend.app.hasn_project.model import HasnProject, HasnProjectMilestone
from backend.app.hasn_project.schema.project_app import ProjectSummary
from backend.app.hasn_project.service.project_linkage_registry import project_linkage_registry
from backend.common.exception import errors

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

# ── 字段白名单（owner_id/id/时间戳/enterprise_id 永不在内；enterprise_id 服务端注入）──────
_PROJECT_FIELDS = {'name', 'goal', 'cover_asset_uri', 'status', 'bound_agent_id'}
_MILESTONE_FIELDS = {'name', 'due_time', 'status', 'artifact_ref', 'sort'}

# 状态白名单（doc38 §12.3：项目 active/archived；里程碑 pending/done，纯业务态无门控）。
_PROJECT_STATUSES = frozenset({'active', 'archived'})
_MILESTONE_STATUSES = frozenset({'pending', 'done'})
_COVER_ASSET_URI_RE = re.compile(r'^hasn://asset/([^/?#]+)$')

# 时间类字段：Agent/Owner API 的 body 是 untyped dict，时间值经 JSON 必为字符串，
# PostgreSQL 不做 timestamptz=varchar 隐式转换，不转则写入报错。
_DATETIME_KEYS = {'due_time'}


def _err(code: str, msg: str, *, http_code: int = 400) -> errors.RequestError:
    """带机器可读 error_code 的业务错误（经统一信封 data 透出）。"""
    return errors.RequestError(code=http_code, msg=msg, data={'error_code': code})


def _as_uuid(value: Any) -> UUID:
    """入参 id 归一为 UUID（已是 UUID 原样；非法格式如实抛业务 400，不 500）。"""
    if isinstance(value, UUID):
        return value
    try:
        return UUID(str(value))
    except (ValueError, AttributeError, TypeError) as e:
        raise _err('invalid_id', '项目/里程碑 id 不是合法 UUID') from e


def _coerce_field(key: str, value: Any) -> Any:
    """时间类字段 ISO 字符串 → datetime（其余原样；非法格式如实抛 ValueError）。"""
    if key in _DATETIME_KEYS and isinstance(value, str):
        return datetime.fromisoformat(value)
    return value


def _pick(fields: set[str], data: dict[str, Any]) -> dict[str, Any]:
    """仅保留白名单字段且值不为 None（里程碑沿用既有局部更新语义）。"""
    return {k: _coerce_field(k, v) for k, v in data.items() if k in fields and v is not None}


def _required_text(value: Any, *, code: str, field_name: str, limit: int) -> str:
    """规范化必填文本；拒绝 null、空白、非字符串和超过表约束的值。"""
    if not isinstance(value, str):
        raise _err(code, f'{field_name}不能为空')
    normalized = value.strip()
    if not normalized:
        raise _err(code, f'{field_name}不能为空')
    if len(normalized) > limit:
        raise _err(code, f'{field_name}长度不能超过 {limit} 个字符')
    return normalized


def _optional_text(value: Any, *, code: str, field_name: str) -> str | None:
    """规范化可清空文本；显式 null 和空白字符串均归一为 null。"""
    if value is None:
        return None
    if not isinstance(value, str):
        raise _err(code, f'{field_name}必须是字符串')
    normalized = value.strip()
    return normalized or None


def serialize(row: Any) -> dict[str, Any]:
    """SQLAlchemy 行 → JSON 安全 dict（UUID→str、datetime→ISO）。

    ⚠️ cover_asset_uri 保留 `hasn://asset/{id}` 原样引用（分身工具面读引用）；owner API 面（U5
    daemon 代理）在序列化边界另经 resolve_assets 换 CDN 签名 URL，不在此处解析（守「不存直链」铁律）。
    """
    out: dict[str, Any] = {}
    # 以 DB 列名作输出键（契约稳定），但用 ORM 属性 key 取值——关键修正：当 DB 列名与 ORM 属性 key
    # 不一致时（如 public.hasn_artifacts 的 `metadata` 列被 declarative 重命名为 `meta_data`，避免撞
    # SQLAlchemy 保留的类级 `metadata`），直接 getattr(row, 列名) 会取到类级 MetaData 对象而非列值，
    # 序列化即炸（产物流并集读命中 HasnArtifacts 时踩到）。故必须经 attr.key 读值。
    mapper = sa.inspect(row).mapper
    for attr in mapper.column_attrs:
        col_name = attr.columns[0].name
        v = getattr(row, attr.key)
        if isinstance(v, UUID):
            out[col_name] = str(v)
        elif isinstance(v, datetime):
            out[col_name] = v.isoformat()
        else:
            out[col_name] = v
    return out


class ProjectService:
    """平台项目 owner 隔离 CRUD + 里程碑管理。所有方法的 owner 由调用方解析后传入。"""

    # ── project ─────────────────────────────────────────────────────────────────
    async def list_projects(self, db: AsyncSession, *, owner: str, status: str | None = None) -> list[dict]:
        """列主人名下项目及稳定摘要（active 在前；聚合绝不按项目逐行查询）。"""
        stmt = sa.select(HasnProject).where(HasnProject.owner_id == owner)
        if status:
            stmt = stmt.where(HasnProject.status == status)
        # active 在前、archived 在后；同状态内按创建时间倒序（新项目在上）。
        stmt = stmt.order_by(HasnProject.status.asc(), HasnProject.created_time.desc())
        rows = (await db.execute(stmt)).scalars().all()
        summaries = await self._project_summaries(db, owner=owner, projects=rows)
        return [summaries[row.id] for row in rows]

    async def _project_summaries(
        self,
        db: AsyncSession,
        *,
        owner: str,
        projects: Sequence[HasnProject],
    ) -> dict[UUID, dict[str, Any]]:
        """以固定数量的 set-based 查询构造项目列表/详情共用摘要。

        这里刻意不调用单项目的产物流或挂靠读取：所有项目 id 一次传入各聚合查询，容器注册表
        也只按 adapter 批量扫描。这样项目数量增长不会引入 N+1，同时统计仍遵循三路产物流口径。
        """
        project_ids = [project.id for project in projects]
        if not project_ids:
            return {}

        link_summaries = await project_linkage_registry.project_link_summaries(
            db,
            owner=owner,
            project_ids=project_ids,
        )
        aggregates: dict[UUID, dict[str, Any]] = {}
        for project in projects:
            link_summary = link_summaries.get(project.id, {})
            aggregates[project.id] = {
                'artifact_ids': set(),
                'agent_ids': {project.bound_agent_id} if project.bound_agent_id else set(),
                'session_count': 0,
                'active_session_count': 0,
                'milestone_done_count': 0,
                'milestone_total_count': 0,
                'link_count': int(link_summary.get('link_count') or 0),
                'linked_apps': dict(link_summary.get('linked_apps') or {}),
                'last_activity_time': link_summary.get('last_activity_time'),
            }

        def record_activity(project_id: UUID, occurred_at: datetime | None) -> None:
            if occurred_at is None or project_id not in aggregates:
                return
            current = aggregates[project_id]['last_activity_time']
            if current is None or occurred_at > current:
                aggregates[project_id]['last_activity_time'] = occurred_at

        milestone_rows = (
            await db.execute(
                sa.select(
                    HasnProjectMilestone.project_id,
                    sa.func.count().label('total_count'),
                    sa.func.count().filter(HasnProjectMilestone.status == 'done').label('done_count'),
                    sa.func.max(
                        sa.func.coalesce(
                            HasnProjectMilestone.updated_time,
                            HasnProjectMilestone.created_time,
                        )
                    ).label('last_activity_time'),
                )
                .where(HasnProjectMilestone.project_id.in_(project_ids))
                .group_by(HasnProjectMilestone.project_id)
            )
        ).all()
        for project_id, total_count, done_count, last_activity_time in milestone_rows:
            aggregate = aggregates[project_id]
            aggregate['milestone_total_count'] = int(total_count)
            aggregate['milestone_done_count'] = int(done_count)
            record_activity(project_id, last_activity_time)

        session_rows = (
            await db.execute(
                sa.select(
                    HasnSessions.project_id,
                    HasnSessions.hasn_id,
                    HasnSessions.session_status,
                    sa.func.coalesce(
                        HasnSessions.last_message_at,
                        HasnSessions.updated_time,
                        HasnSessions.created_time,
                    ).label('last_activity_time'),
                ).where(
                    HasnSessions.owner_id == owner,
                    HasnSessions.project_id.in_(project_ids),
                )
            )
        ).all()
        for project_id, agent_id, session_status, last_activity_time in session_rows:
            aggregate = aggregates[project_id]
            aggregate['session_count'] += 1
            if session_status in {'active', 'waiting_for_user'}:
                aggregate['active_session_count'] += 1
            if agent_id:
                aggregate['agent_ids'].add(agent_id)
            record_activity(project_id, last_activity_time)

        contribution_rows = (
            await db.execute(
                sa.select(
                    HasnArtifactContributions.project_id,
                    HasnArtifactContributions.agent_hasn_id,
                    HasnArtifactContributions.occurred_time,
                ).where(
                    HasnArtifactContributions.owner_hasn_id == owner,
                    HasnArtifactContributions.project_id.in_(project_ids),
                )
            )
        ).all()
        for project_id, agent_id, occurred_time in contribution_rows:
            if agent_id:
                aggregates[project_id]['agent_ids'].add(agent_id)
            record_activity(project_id, occurred_time)

        participating_artifact_rows = (
            await db.execute(
                sa.select(
                    HasnArtifactContributions.project_id,
                    HasnArtifactContributions.artifact_id,
                )
                .join(
                    HasnArtifacts,
                    HasnArtifacts.artifact_id == HasnArtifactContributions.artifact_id,
                )
                .where(
                    HasnArtifactContributions.owner_hasn_id == owner,
                    HasnArtifactContributions.project_id.in_(project_ids),
                    HasnArtifacts.owner_hasn_id == owner,
                    HasnArtifacts.status == 'active',
                )
            )
        ).all()
        for project_id, artifact_id in participating_artifact_rows:
            aggregates[project_id]['artifact_ids'].add(artifact_id)

        explicit_artifact_rows = (
            await db.execute(
                sa.select(
                    HasnArtifacts.project_id,
                    HasnArtifacts.artifact_id,
                    sa.func.coalesce(HasnArtifacts.updated_time, HasnArtifacts.created_time).label('last_activity_time'),
                ).where(
                    HasnArtifacts.owner_hasn_id == owner,
                    HasnArtifacts.project_id.in_(project_ids),
                    HasnArtifacts.status == 'active',
                )
            )
        ).all()
        for project_id, artifact_id, last_activity_time in explicit_artifact_rows:
            aggregates[project_id]['artifact_ids'].add(artifact_id)
            record_activity(project_id, last_activity_time)

        container_pairs = await project_linkage_registry.artifact_resource_uri_pairs(
            db,
            owner=owner,
            project_ids=project_ids,
        )
        resource_projects: dict[str, set[UUID]] = {}
        for project_id, resource_uri in container_pairs:
            resource_projects.setdefault(resource_uri, set()).add(project_id)
        if resource_projects:
            container_artifact_rows = (
                await db.execute(
                    sa.select(
                        HasnArtifacts.resource_uri,
                        HasnArtifacts.artifact_id,
                        sa.func.coalesce(
                            HasnArtifacts.updated_time,
                            HasnArtifacts.created_time,
                        ).label('last_activity_time'),
                    ).where(
                        HasnArtifacts.owner_hasn_id == owner,
                        HasnArtifacts.resource_uri.in_(resource_projects),
                        HasnArtifacts.status == 'active',
                    )
                )
            ).all()
            for resource_uri, artifact_id, last_activity_time in container_artifact_rows:
                for project_id in resource_projects[resource_uri]:
                    aggregates[project_id]['artifact_ids'].add(artifact_id)
                    record_activity(project_id, last_activity_time)

        summaries: dict[UUID, dict[str, Any]] = {}
        for project in projects:
            aggregate = aggregates[project.id]
            agent_ids = set(aggregate['agent_ids'])
            ordered_agent_ids = (
                [project.bound_agent_id] if project.bound_agent_id else []
            ) + sorted(agent_ids - {project.bound_agent_id})
            linked_apps = [
                {'app_id': app_id, 'count': count}
                for app_id, count in sorted(aggregate['linked_apps'].items())
            ]
            summary = ProjectSummary.model_validate(
                {
                    **serialize(project),
                    'artifact_count': len(aggregate['artifact_ids']),
                    'session_count': aggregate['session_count'],
                    'active_session_count': aggregate['active_session_count'],
                    'agent_count': len(agent_ids),
                    'link_count': aggregate['link_count'],
                    'milestone_done_count': aggregate['milestone_done_count'],
                    'milestone_total_count': aggregate['milestone_total_count'],
                    'agent_ids': ordered_agent_ids,
                    'linked_apps': linked_apps,
                    'last_activity_time': aggregate['last_activity_time'],
                }
            )
            summaries[project.id] = summary.model_dump(mode='json')
        return summaries

    async def get_owned_project(self, db: AsyncSession, *, owner: str, pk: str | UUID) -> HasnProject:
        """按 (owner, id) 取项目行；不存在/非本人 → 404（owner 隔离兜死，不泄漏他人项目存在性）。"""
        row = (
            await db.execute(
                sa.select(HasnProject).where(HasnProject.id == _as_uuid(pk), HasnProject.owner_id == owner)
            )
        ).scalar_one_or_none()
        if row is None:
            raise errors.NotFoundError(msg='项目不存在')
        return row

    async def _validate_project_fields(
        self, db: AsyncSession, *, owner: str, data: dict[str, Any], creating: bool = False
    ) -> dict[str, Any]:
        """校验并规范化项目写入字段，Owner API 与 MCP 必须共用此处。

        使用「字段是否出现」而非值是否为 ``None`` 判定 PATCH 意图：省略字段保持原值，
        显式 ``null`` 则按字段契约清空。封面和默认分身均在写入前按主人归属查权威表。
        """
        fields = {key: data[key] for key in _PROJECT_FIELDS if key in data}
        if creating and 'name' not in fields:
            raise _err('INVALID_PROJECT_NAME', '项目名不能为空')

        if 'name' in fields:
            fields['name'] = _required_text(
                fields['name'], code='INVALID_PROJECT_NAME', field_name='项目名', limit=200
            )
        if 'goal' in fields:
            fields['goal'] = _optional_text(
                fields['goal'], code='INVALID_PROJECT_GOAL', field_name='项目目标'
            )
        if 'status' in fields:
            status = fields['status']
            if not isinstance(status, str) or status not in _PROJECT_STATUSES:
                raise _err('INVALID_PROJECT_STATUS', f'项目状态非法（仅 {sorted(_PROJECT_STATUSES)}）')

        if 'cover_asset_uri' in fields:
            cover_asset_uri = _optional_text(
                fields['cover_asset_uri'], code='INVALID_COVER_ASSET', field_name='项目封面'
            )
            if cover_asset_uri is not None:
                matched = _COVER_ASSET_URI_RE.fullmatch(cover_asset_uri)
                if matched is None:
                    raise _err(
                        'INVALID_COVER_ASSET',
                        '项目封面只接受主人名下的 hasn://asset/{id} 引用',
                        http_code=422,
                    )
                asset = (
                    await db.execute(
                        sa.select(HasnAssets.id).where(
                            HasnAssets.asset_id == matched.group(1),
                            HasnAssets.owner_hasn_id == owner,
                        )
                    )
                ).scalar_one_or_none()
                if asset is None:
                    raise _err('INVALID_COVER_ASSET', '项目封面资产不存在或不属于当前主人', http_code=422)
            fields['cover_asset_uri'] = cover_asset_uri

        if 'bound_agent_id' in fields:
            bound_agent_id = _optional_text(
                fields['bound_agent_id'], code='INVALID_BOUND_AGENT', field_name='默认协作分身'
            )
            if bound_agent_id is not None:
                agent = (
                    await db.execute(
                        sa.select(HasnAgents.id).where(
                            HasnAgents.hasn_id == bound_agent_id,
                            HasnAgents.owner_id == owner,
                            HasnAgents.deleted_at.is_(None),
                        )
                    )
                ).scalar_one_or_none()
                if agent is None:
                    raise _err('INVALID_BOUND_AGENT', '默认协作分身不存在或不属于当前主人', http_code=422)
            fields['bound_agent_id'] = bound_agent_id
        return fields

    async def resolve_active_project_for_work(
        self, db: AsyncSession, *, owner: str, pk: str | UUID
    ) -> HasnProject:
        """校验可新增项目工作：归档项目仍可读和恢复，但不能新增资源或里程碑。"""
        row = await self.get_owned_project(db, owner=owner, pk=pk)
        if row.status == 'archived':
            raise _err('PROJECT_ARCHIVED', '项目已归档，不能新增项目工作', http_code=409)
        return row

    async def assert_active_owned_agent(self, db: AsyncSession, *, owner: str, agent_id: str) -> None:
        """确认当前分身仍活跃且属于该主人，避免 Agent JWT 误写到其它主人项目。"""
        agent = (
            await db.execute(
                sa.select(HasnAgents.id).where(
                    HasnAgents.hasn_id == agent_id,
                    HasnAgents.owner_id == owner,
                    HasnAgents.status == 'active',
                    HasnAgents.deleted_at.is_(None),
                )
            )
        ).scalar_one_or_none()
        if agent is None:
            raise _err('INVALID_PROJECT_AGENT', '当前分身不存在、已停用或不属于当前主人', http_code=403)

    async def get_project(self, db: AsyncSession, *, owner: str, pk: str | UUID) -> dict:
        """取单个项目详情（含里程碑轨与注册表派生的挂靠资源）。"""
        row = await self.get_owned_project(db, owner=owner, pk=pk)
        summary = (await self._project_summaries(db, owner=owner, projects=[row]))[row.id]
        data = {**serialize(row), **summary, 'summary': summary}
        data['milestones'] = await self.list_milestones(db, owner=owner, project_id=row.id)
        data['linked_resources'] = await project_linkage_registry.list_linked_resources(
            db,
            owner=owner,
            project_id=row.id,
        )
        recent_sessions = await hasn_sessions_service.list_work_session_summaries(
            db=db,
            owner_id=owner,
            project_id=row.id,
            limit=10,
        )
        # `recent_sessions` 是稳定契约；保留 `sessions` 同一真实摘要的别名，供现有 WebUI 无缝升级。
        data['recent_sessions'] = recent_sessions
        data['sessions'] = recent_sessions
        # 巡检建议只展示未处理项；全部历史由显式 Owner list API 提供，防止处理过的卡片反复出现。
        from backend.app.hasn_project.service.hasn_project_inspection_service import inspection_service

        data['inspections'] = await inspection_service.list_for_project(
            db,
            owner=owner,
            project_id=row.id,
            status='unread',
        )
        data['inspection_schedule'] = await inspection_service.inspection_schedule(
            db,
            owner=owner,
            project_id=row.id,
        )
        data['linkable_domains'] = project_linkage_registry.linkable_domains()
        return data

    async def assert_owned(self, db: AsyncSession, *, owner: str, pk: str | UUID) -> None:
        """校验项目归属（不存在/非本人 → 404）。link/unlink 前置校验用（挂靠不写他人项目）。"""
        await self.get_owned_project(db, owner=owner, pk=pk)

    async def resolve_open_for_new_workflow(
        self, db: AsyncSession, *, owner: str, project_id: str | UUID
    ) -> HasnProject:
        """场景工作流实例化硬闸的项目校验（doc95 §2.1）：解析平台项目并确认可在其中新开场景。

        刻意**不按 owner 过滤查询**——要区分「项目不存在」与「项目存在但属他人」两种情况：
        1. 不存在 → 404；
        2. 跨 owner → **403**（不是 404，不做存在性隐藏——项目不是权限边界，越权就是越权）；
        3. 归档项目 → 结构化拒绝（archived 可读不可新开，与 doc38 §6 一致）。
        """
        pk = _as_uuid(project_id)
        row = (
            await db.execute(sa.select(HasnProject).where(HasnProject.id == pk))
        ).scalar_one_or_none()
        if row is None:
            raise errors.NotFoundError(msg='平台项目不存在')
        if row.owner_id != owner:
            # 跨 owner：项目不是权限边界，越权即越权 → 403（非 404，不做存在性隐藏假装不存在）
            raise errors.ForbiddenError(
                msg='无权在他人的项目中开启场景', data={'error_code': 'project_cross_owner'}
            )
        if row.status == 'archived':
            raise _err('PROJECT_ARCHIVED', '项目已归档，不能在其中开启新场景', http_code=409)
        return row

    async def create_project(
        self, db: AsyncSession, *, owner: str, data: dict, enterprise_id: str | UUID | None = None
    ) -> dict:
        """建项目（name 必填）。enterprise_id 由服务端解析后传入，绝不来自 data。"""
        fields = await self._validate_project_fields(db, owner=owner, data=data, creating=True)
        row = HasnProject(owner_id=owner, enterprise_id=_as_uuid(enterprise_id) if enterprise_id else None, **fields)
        db.add(row)
        await db.flush()
        await db.refresh(row)
        return serialize(row)

    async def update_project(self, db: AsyncSession, *, owner: str, pk: str | UUID, data: dict) -> dict:
        """改项目（省略保持原值、显式 null 清空；归档时只允许恢复为 active）。"""
        row = await self.get_owned_project(db, owner=owner, pk=pk)
        fields = await self._validate_project_fields(db, owner=owner, data=data)
        if row.status == 'archived' and fields and fields != {'status': 'active'}:
            raise _err('PROJECT_ARCHIVED', '项目已归档，仅可恢复为进行中', http_code=409)
        for k, v in fields.items():
            setattr(row, k, v)
        await db.flush()
        await db.refresh(row)
        return serialize(row)

    async def archive_project(self, db: AsyncSession, *, owner: str, pk: str | UUID) -> dict:
        """归档项目（status→archived）。归档只是状态标记，不删数据、不断挂靠（doc38：项目非权限边界）。"""
        row = await self.get_owned_project(db, owner=owner, pk=pk)
        row.status = 'archived'
        await db.flush()
        await db.refresh(row)
        return serialize(row)

    # ── milestone（纯业务状态标记·无依赖无门控，doc38 第四铁律）────────────────────────
    async def list_milestones(self, db: AsyncSession, *, owner: str, project_id: str | UUID) -> list[dict]:
        """列某项目里程碑（横向轨次序：sort 升序、到期时间升序）。先校验项目归属。"""
        await self.get_owned_project(db, owner=owner, pk=project_id)
        stmt = (
            sa.select(HasnProjectMilestone)
            .where(HasnProjectMilestone.project_id == _as_uuid(project_id))
            .order_by(HasnProjectMilestone.sort.asc(), HasnProjectMilestone.due_time.asc().nullslast())
        )
        rows = (await db.execute(stmt)).scalars().all()
        return [serialize(r) for r in rows]

    async def _get_owned_milestone(
        self, db: AsyncSession, *, owner: str, milestone_id: int
    ) -> HasnProjectMilestone:
        """按 milestone_id 取行并经父项目校验 owner 归属（跨 owner → 404）。"""
        row = (
            await db.execute(
                sa.select(HasnProjectMilestone).where(HasnProjectMilestone.id == int(milestone_id))
            )
        ).scalar_one_or_none()
        if row is None:
            raise errors.NotFoundError(msg='里程碑不存在')
        # 经父项目归属校验（不存在/非本人即抛 404）。
        await self.get_owned_project(db, owner=owner, pk=row.project_id)
        return row

    async def create_milestone(self, db: AsyncSession, *, owner: str, project_id: str | UUID, data: dict) -> dict:
        """在项目下建里程碑（name 必填）。先校验项目归属。"""
        await self.resolve_active_project_for_work(db, owner=owner, pk=project_id)
        fields = _pick(_MILESTONE_FIELDS, data)
        name = str(fields.get('name') or '').strip()
        if not name:
            raise _err('name_required', '里程碑名不能为空')
        fields['name'] = name
        status = fields.get('status')
        if status is not None and status not in _MILESTONE_STATUSES:
            raise _err('invalid_status', f'里程碑状态非法（仅 {sorted(_MILESTONE_STATUSES)}）')
        row = HasnProjectMilestone(project_id=_as_uuid(project_id), **fields)
        db.add(row)
        await db.flush()
        await db.refresh(row)
        return serialize(row)

    async def update_milestone(self, db: AsyncSession, *, owner: str, milestone_id: int, data: dict) -> dict:
        """改里程碑（名/到期/状态/关联产物/排序；经父项目校验 owner）。"""
        row = await self._get_owned_milestone(db, owner=owner, milestone_id=milestone_id)
        fields = _pick(_MILESTONE_FIELDS, data)
        status = fields.get('status')
        if status is not None and status not in _MILESTONE_STATUSES:
            raise _err('invalid_status', f'里程碑状态非法（仅 {sorted(_MILESTONE_STATUSES)}）')
        for k, v in fields.items():
            setattr(row, k, v)
        await db.flush()
        await db.refresh(row)
        return serialize(row)

    async def complete_milestone(self, db: AsyncSession, *, owner: str, milestone_id: int) -> dict:
        """完成里程碑（status→done）。纯业务态标记，不触发任何门控/依赖检查（doc38 第四铁律）。"""
        row = await self._get_owned_milestone(db, owner=owner, milestone_id=milestone_id)
        row.status = 'done'
        await db.flush()
        await db.refresh(row)
        return serialize(row)

    # ── 产物流并集读（doc38 §5 item 6）────────────────────────────────────────────
    async def project_artifact_flow(
        self,
        db: AsyncSession,
        *,
        owner: str,
        project_id: str | UUID,
        page: int = 1,
        size: int = 50,
    ) -> dict[str, Any]:
        """委托唯一权威产物查询，返回三路并集分页信封。

        项目流不能再维护第二套 SQL。`artifact_query_service` 统一负责历史参与、当前显式
        project_id 与挂靠容器子资源的去重、排序、分页和 `project_relation.via`，项目 service
        只保留 owner 项目存在性校验与 JSON 序列化边界。
        """
        await self.get_owned_project(db, owner=owner, pk=project_id)  # 归属校验（非本人 → 404）
        result = await artifact_query_service.list(
            db,
            owner_hasn_id=owner,
            project_id=str(_as_uuid(project_id)),
            page=page,
            size=size,
        )
        return result.model_dump(mode='json')


project_service = ProjectService()
