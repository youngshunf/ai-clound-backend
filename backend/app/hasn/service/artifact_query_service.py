"""Agent 产物唯一查询服务。"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime
from typing import TYPE_CHECKING, Any, Literal

from uuid import UUID

from sqlalchemy import and_, case, exists, func, or_, select
from sqlalchemy.orm import aliased

from backend.app.hasn.model import HasnAgents, HasnArtifactContributions, HasnArtifacts, HasnHumans
from backend.app.hasn.schema.artifact_contract import (
    ArtifactAvailability,
    ArtifactAgentIdentity,
    ArtifactListItem,
    ArtifactListPage,
    ArtifactProjectRelation,
    LatestContribution,
    LocalArtifactEntry,
)
from backend.app.hasn.service.hasn_asset_service import hasn_asset_service
from backend.app.hasn_project.service.project_linkage_registry import project_linkage_registry
from backend.common.exception import errors
from backend.common.log import log

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession


def encode_keyset_cursor(updated_time: datetime, artifact_id: str) -> str:
    """把排序键末行编码为 keyset 游标（设计 02 §8.2/A16）。

    格式 `{isoformat}|{artifact_id}`：artifact_id 是 ULID/文本、不含 `|`；
    ISO 时间戳保留时区，decode 回同瞬时不漂移。
    """
    return f'{updated_time.isoformat()}|{artifact_id}'


def decode_keyset_cursor(cursor: str) -> tuple[datetime, str]:
    """解码 keyset 游标；损坏/手改的游标一律 422，不静默退回第一页（那会丢页还看似正常）。"""
    raw = (cursor or '').strip()
    if not raw or '|' not in raw:
        raise errors.RequestError(msg='cursor 格式无效')
    ts_text, _, artifact_id = raw.rpartition('|')
    if not artifact_id:
        raise errors.RequestError(msg='cursor 格式无效')
    try:
        updated_time = datetime.fromisoformat(ts_text)
    except ValueError:
        raise errors.RequestError(msg='cursor 格式无效') from None
    return updated_time, artifact_id


class ArtifactQueryService:
    """将当前态和筛选上下文内的最新参与记录组装为唯一读模型。"""

    @staticmethod
    def _source_link(contribution: HasnArtifactContributions) -> str | None:
        """由参与记录生成客户端无关的来源跳转 URI。"""
        if contribution.conversation_id:
            conversation_id = str(contribution.conversation_id)
            if contribution.message_id:
                return f'hasn://messages/c/{conversation_id}#{contribution.message_id}'
            return f'hasn://messages/c/{conversation_id}'
        if contribution.work_session_id:
            return f'hasn://tasks/sessions/{contribution.work_session_id}'
        return None

    @staticmethod
    def _availability(
        artifact: HasnArtifacts, current_node_id: str | None
    ) -> tuple[ArtifactAvailability, list[Literal['open', 'preview', 'download', 'locate']]]:
        """仅据权威当前态返回可验证的可用性和动作，不猜测本地文件是否存在。"""
        if artifact.status == 'missing':
            return 'missing', []
        if artifact.source_asset_uri:
            return 'cloud', ['preview', 'download']
        if artifact.local_locator_key:
            # P06 只持久化不可逆 locator，尚无经路径守卫校验的本机反查端点。
            # 因此即便 node_id 匹配，也不能伪称可定位或返回不可执行的 locate 动作。
            if current_node_id and artifact.node_id == current_node_id:
                return 'local_unavailable', []
            return 'local_other_device', []
        if artifact.asset_id:
            return 'cloud', ['preview', 'download']
        return 'cloud', ['open']

    @staticmethod
    def _source_asset_id(source_asset_uri: str | None) -> str | None:
        """从已校验的私有快照 URI 提取 asset_id；异常存量保持不可预览。"""
        prefix = 'hasn://asset/'
        if not source_asset_uri or not source_asset_uri.startswith(prefix):
            return None
        asset_id = source_asset_uri.removeprefix(prefix)
        return asset_id if asset_id and '/' not in asset_id else None

    async def _signed_urls(
        self, db: AsyncSession, *, owner_hasn_id: str, asset_ids: Sequence[str]
    ) -> dict[str, str]:
        """批量解析私有资产签名 URL，避免逐行访问资产服务。"""
        ids = list(dict.fromkeys(asset_id for asset_id in asset_ids if asset_id))
        if not ids:
            return {}
        resolved = await hasn_asset_service.resolve(
            db,
            requester_hasn_id=owner_hasn_id,
            asset_ids=ids,
            conversation_id=None,
        )
        return {item.asset_id: item.display_url for item in resolved}

    async def list(
        self,
        db: AsyncSession,
        *,
        owner_hasn_id: str,
        agent_hasn_id: str | None = None,
        work_session_id: str | None = None,
        project_id: str | None = None,
        origin_ref: str | None = None,
        artifact_id: str | None = None,
        artifact_kind: str | None = None,
        source_kind: str | None = None,
        source_app_id: str | None = None,
        resource_kind: str | None = None,
        keyword: str | None = None,
        status: str = 'active',
        page: int = 1,
        size: int = 20,
        current_node_id: str | None = None,
        cursor: str | None = None,
    ) -> ArtifactListPage:
        """按 owner 和可选上下文读取当前态，并选出该上下文内最新参与记录。

        排序键统一 `(updated_time DESC, artifact_id DESC)`（设计 02 §8.2，与本地
        `idx_agent_artifacts_owner_updated` 对齐）——daemon 本地优先合并两个分页源时
        只有同一排序键才能稳定去重。`cursor` 非空时按 keyset 过滤（编码见
        `encode_keyset_cursor`），与 `page` 叠加语义为「先 keyset 再偏移」，daemon
        聚合只传其一。
        """
        page = max(1, page)
        size = max(1, min(size, 100))

        project_uuid: UUID | None = None
        container_uris: set[str] = set()
        if project_id:
            try:
                project_uuid = UUID(project_id)
            except (TypeError, ValueError):
                return ArtifactListPage(items=[], total=0, page=page, size=size)

            container_uris = set(
                await project_linkage_registry.artifact_resource_uris(
                    db,
                    owner=owner_hasn_id,
                    project_id=project_uuid,
                )
            )

        contribution_conditions = [HasnArtifactContributions.owner_hasn_id == owner_hasn_id]
        if agent_hasn_id:
            contribution_conditions.append(HasnArtifactContributions.agent_hasn_id == agent_hasn_id)
        if work_session_id:
            contribution_conditions.append(HasnArtifactContributions.work_session_id == work_session_id)
        if source_kind:
            contribution_conditions.append(HasnArtifactContributions.source_kind == source_kind)
        if source_app_id:
            contribution_conditions.append(HasnArtifactContributions.source_app_id == source_app_id)

        rank_order: tuple[Any, ...]
        if project_uuid:
            # 项目视图优先展示该项目内最新参与；只经资源关联/容器关联命中的产物没有项目参与时，
            # 再回落到其当前最新参与，避免把项目产物伪造成“参与记录”。
            rank_order = (
                case((HasnArtifactContributions.project_id == project_uuid, 0), else_=1).asc(),
                HasnArtifactContributions.occurred_time.desc(),
                HasnArtifactContributions.id.desc(),
            )
        else:
            rank_order = (
                HasnArtifactContributions.occurred_time.desc(),
                HasnArtifactContributions.id.desc(),
            )

        ranked = (
            select(
                HasnArtifactContributions.contribution_id.label('contribution_id'),
                func.row_number()
                .over(
                    partition_by=HasnArtifactContributions.artifact_id,
                    order_by=rank_order,
                )
                .label('rank'),
            )
            .where(*contribution_conditions)
            .subquery()
        )

        # A15：无参与轴筛选时用 LEFT JOIN——「历史回填无法恢复任何参与事实」的行也必须
        # 出现在主人全量列表里（latest_contribution=None + migration_lost_history 诚实标记），
        # 而不是被 INNER JOIN 静默吞掉；一旦按分身/会话/来源轴筛选，无参与记录的行天然
        # 不可能命中该轴，退回 INNER JOIN 保持原语义。
        has_contribution_axis = bool(agent_hasn_id or work_session_id or source_kind or source_app_id)

        artifact_status_condition = (
            HasnArtifacts.status.in_(('active', 'missing'))
            if status == 'active'
            else HasnArtifacts.status == status
        )
        artifact_conditions = [
            HasnArtifacts.owner_hasn_id == owner_hasn_id,
            artifact_status_condition,
        ]
        if has_contribution_axis:
            artifact_conditions.append(ranked.c.rank == 1)
        else:
            # 无参与记录的行 rank 为 NULL，靠 OR 逃生口保留；有参与记录的行仍只取 rank=1。
            artifact_conditions.append(or_(ranked.c.rank == 1, ranked.c.contribution_id.is_(None)))
        if artifact_kind:
            artifact_conditions.append(HasnArtifacts.artifact_kind == artifact_kind)
        if artifact_id:
            artifact_conditions.append(HasnArtifacts.artifact_id == artifact_id)
        if resource_kind:
            artifact_conditions.append(HasnArtifacts.resource_kind == resource_kind)
        if origin_ref:
            artifact_conditions.append(HasnArtifacts.origin_ref == origin_ref)
        if project_uuid:
            project_contribution = aliased(HasnArtifactContributions)
            participation_exists = exists(
                select(1).where(
                    project_contribution.owner_hasn_id == owner_hasn_id,
                    project_contribution.artifact_id == HasnArtifacts.artifact_id,
                    project_contribution.project_id == project_uuid,
                )
            )
            project_relations = [
                participation_exists,
                HasnArtifacts.project_id == project_uuid,
            ]
            if container_uris:
                project_relations.append(HasnArtifacts.resource_uri.in_(container_uris))
            artifact_conditions.append(or_(*project_relations))
        if keyword:
            for word in keyword.split():
                escaped = word.replace('\\', '\\\\').replace('%', '\\%').replace('_', '\\_')
                pattern = f'%{escaped}%'
                artifact_conditions.append(
                    or_(
                        HasnArtifacts.title.ilike(pattern, escape='\\'),
                        HasnArtifacts.summary.ilike(pattern, escape='\\'),
                    )
                )

        statement = select(HasnArtifacts, HasnArtifactContributions)
        if has_contribution_axis:
            statement = statement.join(
                HasnArtifactContributions,
                HasnArtifactContributions.artifact_id == HasnArtifacts.artifact_id,
            ).join(ranked, ranked.c.contribution_id == HasnArtifactContributions.contribution_id)
        else:
            statement = statement.outerjoin(
                HasnArtifactContributions,
                HasnArtifactContributions.artifact_id == HasnArtifacts.artifact_id,
            ).outerjoin(ranked, ranked.c.contribution_id == HasnArtifactContributions.contribution_id)
        statement = statement.where(*artifact_conditions)
        total = (await db.execute(select(func.count()).select_from(statement.subquery()))).scalar_one()

        # 排序键对齐设计 02 §8.2：`(updated_time DESC, artifact_id DESC)`，与本地索引
        # `idx_agent_artifacts_owner_updated` 同键——daemon 合并本地/云端两源 keyset 分页时
        # 靠同键稳定去重。`updated_time` 可空，统一回落 `created_time`（DTO 同口径）。
        sort_key = func.coalesce(HasnArtifacts.updated_time, HasnArtifacts.created_time)
        if cursor:
            cursor_time, cursor_artifact_id = decode_keyset_cursor(cursor)
            statement = statement.where(
                or_(
                    sort_key < cursor_time,
                    and_(sort_key == cursor_time, HasnArtifacts.artifact_id < cursor_artifact_id),
                )
            )

        rows = (
            await db.execute(
                statement.order_by(
                    sort_key.desc(),
                    HasnArtifacts.artifact_id.desc(),
                )
                .offset((page - 1) * size)
                .limit(size)
            )
        ).all()

        snapshot_asset_ids = [
            asset_id
            for artifact, _contribution in rows
            if (asset_id := self._source_asset_id(artifact.source_asset_uri))
        ]
        urls = await self._signed_urls(
            db,
            owner_hasn_id=owner_hasn_id,
            asset_ids=[
                *[artifact.asset_id for artifact, _contribution in rows if artifact.asset_id],
                *snapshot_asset_ids,
            ],
        )
        agent_ids = list(
            {contribution.agent_hasn_id for _artifact, contribution in rows if contribution is not None}
        )
        agents = {
            agent.hasn_id: agent
            for agent in (
                await db.execute(select(HasnAgents).where(HasnAgents.hasn_id.in_(agent_ids)))
            ).scalars()
        } if agent_ids else {}
        owner_names = {
            human.hasn_id: human.nickname
            for human in (
                await db.execute(
                    select(HasnHumans).where(HasnHumans.hasn_id.in_({owner_hasn_id}))
                )
            ).scalars()
        }

        items: list[ArtifactListItem] = []
        for artifact, contribution in rows:
            availability, allowed_actions = self._availability(artifact, current_node_id)
            identity = None
            if contribution is not None:
                agent = agents.get(contribution.agent_hasn_id)
                if agent is not None:
                    identity = ArtifactAgentIdentity(
                        hasn_id=agent.hasn_id,
                        display_name=agent.display_name or None,
                        avatar_url=agent.avatar,
                        profession=agent.profession,
                        owner_name=owner_names.get(artifact.owner_hasn_id) or None,
                    )
            project_relation = None
            if project_uuid:
                if contribution is not None and contribution.project_id == project_uuid:
                    project_relation = ArtifactProjectRelation(
                        project_id=str(project_uuid), via='participation'
                    )
                elif artifact.project_id == project_uuid:
                    project_relation = ArtifactProjectRelation(
                        project_id=str(project_uuid), via='explicit_resource_link'
                    )
                elif artifact.resource_uri in container_uris:
                    project_relation = ArtifactProjectRelation(
                        project_id=str(project_uuid), via='linked_container'
                    )
            local_entry = None
            if artifact.local_locator_key and artifact.node_id and artifact.local_entry_kind:
                local_entry = LocalArtifactEntry(
                    node_id=artifact.node_id,
                    entry_kind=artifact.local_entry_kind,
                    device_name=None,
                )
            source_asset_id = self._source_asset_id(artifact.source_asset_uri)
            signed_url = (
                urls.get(artifact.asset_id)
                if artifact.asset_id
                else urls.get(source_asset_id) if source_asset_id else None
            )
            # A15：latest_contribution 只在「历史回填无法恢复参与事实」时合法为 None，
            # 此时 migration_lost_history 必须已在库内打好（2026-07-29 回填迁移）；缺参与
            # 又没标记的是登记链路缺陷——warn 显式告警（不伪填、不吞行），行仍如实透出。
            migration_lost_history = bool((artifact.meta_data or {}).get('migration_lost_history'))
            latest_contribution = None
            if contribution is not None:
                latest_contribution = LatestContribution(
                    contribution_id=contribution.contribution_id,
                    agent_hasn_id=contribution.agent_hasn_id,
                    work_session_id=contribution.work_session_id,
                    project_id=str(contribution.project_id) if contribution.project_id else None,
                    action=contribution.action,
                    source_kind=contribution.source_kind,
                    source_tool=contribution.source_tool,
                    source_app_id=contribution.source_app_id,
                    source_link=self._source_link(contribution),
                    occurred_time=contribution.occurred_time,
                )
            elif not migration_lost_history:
                log.warning(
                    '产物无任何参与记录且未打 migration_lost_history 标记（登记链路缺陷，A15）：%s',
                    artifact.artifact_id,
                )
            items.append(
                ArtifactListItem(
                    artifact_id=artifact.artifact_id,
                    artifact_kind=artifact.artifact_kind,
                    resource_kind=artifact.resource_kind,
                    resource_app_id=artifact.resource_app_id,
                    title=artifact.title,
                    summary=artifact.summary,
                    body_preview=(artifact.body[:240] if artifact.body else None),
                    asset_uri=(f'hasn://asset/{artifact.asset_id}' if artifact.asset_id else None),
                    preview_url=signed_url,
                    download_url=signed_url,
                    resource_uri=artifact.resource_uri,
                    source_asset_uri=artifact.source_asset_uri,
                    source_hash=artifact.source_hash,
                    source_synced_at=artifact.source_synced_at,
                    local_entry=local_entry,
                    availability=availability,
                    allowed_actions=allowed_actions,
                    sync_state='synced',
                    latest_contribution=latest_contribution,
                    migration_lost_history=migration_lost_history,
                    agent_identity=identity,
                    project_relation=project_relation,
                    created_time=artifact.created_time,
                    updated_time=artifact.updated_time or artifact.created_time,
                )
            )
        return ArtifactListPage(items=items, total=total, page=page, size=size)


artifact_query_service = ArtifactQueryService()
