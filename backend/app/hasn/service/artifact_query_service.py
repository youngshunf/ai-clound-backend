"""Agent 产物唯一查询服务。"""

from __future__ import annotations

from collections.abc import Sequence
from typing import TYPE_CHECKING, Literal

from sqlalchemy import func, or_, select

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

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession


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
        if artifact.local_locator_key:
            if current_node_id and artifact.node_id == current_node_id:
                return 'local_current_device', ['locate']
            return 'local_other_device', []
        if artifact.asset_id:
            return 'cloud', ['preview', 'download']
        return 'cloud', ['open']

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
    ) -> ArtifactListPage:
        """按 owner 和可选上下文读取当前态，并选出该上下文内最新参与记录。"""
        contribution_conditions = [HasnArtifactContributions.owner_hasn_id == owner_hasn_id]
        if agent_hasn_id:
            contribution_conditions.append(HasnArtifactContributions.agent_hasn_id == agent_hasn_id)
        if work_session_id:
            contribution_conditions.append(HasnArtifactContributions.work_session_id == work_session_id)
        if project_id:
            contribution_conditions.append(HasnArtifactContributions.project_id == project_id)
        if source_kind:
            contribution_conditions.append(HasnArtifactContributions.source_kind == source_kind)
        if source_app_id:
            contribution_conditions.append(HasnArtifactContributions.source_app_id == source_app_id)

        ranked = (
            select(
                HasnArtifactContributions.contribution_id.label('contribution_id'),
                func.row_number()
                .over(
                    partition_by=HasnArtifactContributions.artifact_id,
                    order_by=(
                        HasnArtifactContributions.occurred_time.desc(),
                        HasnArtifactContributions.id.desc(),
                    ),
                )
                .label('rank'),
            )
            .where(*contribution_conditions)
            .subquery()
        )

        artifact_conditions = [
            HasnArtifacts.owner_hasn_id == owner_hasn_id,
            HasnArtifacts.status == status,
            ranked.c.rank == 1,
        ]
        if artifact_kind:
            artifact_conditions.append(HasnArtifacts.artifact_kind == artifact_kind)
        if artifact_id:
            artifact_conditions.append(HasnArtifacts.artifact_id == artifact_id)
        if resource_kind:
            artifact_conditions.append(HasnArtifacts.resource_kind == resource_kind)
        if origin_ref:
            artifact_conditions.append(HasnArtifacts.origin_ref == origin_ref)
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

        statement = (
            select(HasnArtifacts, HasnArtifactContributions)
            .join(
                HasnArtifactContributions,
                HasnArtifactContributions.artifact_id == HasnArtifacts.artifact_id,
            )
            .join(ranked, ranked.c.contribution_id == HasnArtifactContributions.contribution_id)
            .where(*artifact_conditions)
        )
        total = (await db.execute(select(func.count()).select_from(statement.subquery()))).scalar_one()

        page = max(1, page)
        size = max(1, min(size, 100))
        rows = (
            await db.execute(
                statement.order_by(
                    HasnArtifactContributions.occurred_time.desc(),
                    HasnArtifactContributions.id.desc(),
                )
                .offset((page - 1) * size)
                .limit(size)
            )
        ).all()

        urls = await self._signed_urls(
            db,
            owner_hasn_id=owner_hasn_id,
            asset_ids=[artifact.asset_id for artifact, _contribution in rows if artifact.asset_id],
        )
        agent_ids = list({contribution.agent_hasn_id for _artifact, contribution in rows})
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
            agent = agents.get(contribution.agent_hasn_id)
            identity = None
            if agent is not None:
                identity = ArtifactAgentIdentity(
                    hasn_id=agent.hasn_id,
                    display_name=agent.display_name or None,
                    avatar_url=agent.avatar,
                    profession=agent.profession,
                    owner_name=owner_names.get(artifact.owner_hasn_id) or None,
                )
            project_relation = None
            if project_id and contribution.project_id and str(contribution.project_id) == project_id:
                project_relation = ArtifactProjectRelation(project_id=project_id, via='participation')
            local_entry = None
            if artifact.local_locator_key and artifact.node_id and artifact.local_entry_kind:
                local_entry = LocalArtifactEntry(
                    node_id=artifact.node_id,
                    entry_kind=artifact.local_entry_kind,
                    device_name=None,
                )
            signed_url = urls.get(artifact.asset_id) if artifact.asset_id else None
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
                    local_entry=local_entry,
                    availability=availability,
                    allowed_actions=allowed_actions,
                    sync_state='synced',
                    latest_contribution=LatestContribution(
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
                    ),
                    agent_identity=identity,
                    project_relation=project_relation,
                    created_time=artifact.created_time,
                    updated_time=artifact.updated_time or artifact.created_time,
                )
            )
        return ArtifactListPage(items=items, total=total, page=page, size=size)


artifact_query_service = ArtifactQueryService()
