"""Agent 产物当前态、参与记录与可靠登记意图的统一写入服务。"""

from __future__ import annotations

from dataclasses import dataclass
from uuid import UUID, uuid4

from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.hasn.model import (
    HasnArtifactContributions,
    HasnArtifactRegistrationOutbox,
    HasnArtifacts,
)
from backend.app.hasn.schema.artifact_contract import ArtifactMutation


@dataclass(frozen=True)
class ArtifactRegistrationResult:
    """登记完成后的云端权威产物身份。"""

    artifact_id: str
    resource_uri: str | None


class ArtifactRegistrationService:
    """把一次写入归一为当前态、不可变参与记录和可对账登记意图。"""

    @staticmethod
    def _public_id(prefix: str) -> str:
        """生成长度受控的云端公开标识。"""
        return f'{prefix}_{uuid4().hex}'

    @staticmethod
    def _uuid_or_none(value: str | None) -> UUID | None:
        """将可选 UUID 字符串转换为数据库值，非法值明确拒绝。"""
        return UUID(value) if value else None

    @staticmethod
    def _artifact_key(mutation: ArtifactMutation) -> str:
        """从本体定位方式派生 owner 内稳定对象键。"""
        if mutation.resource_uri:
            return f'resource:{mutation.resource_uri}'
        if mutation.asset_id:
            return f'asset:{mutation.asset_id}'
        if mutation.local_locator_key:
            return f'local:{mutation.node_id}:{mutation.local_locator_key}'
        stable_id = mutation.source_event_id or mutation.dispatch_id or uuid4().hex
        return f'body:{mutation.source_app_id or "agent"}:{stable_id}'

    @staticmethod
    def _contribution_idempotency_key(mutation: ArtifactMutation, artifact_key: str) -> str:
        """按本体类别派生可重放的参与记录幂等键。"""
        if mutation.resource_uri and mutation.dispatch_id:
            return f'resource:{mutation.dispatch_id}:{mutation.resource_uri}'
        if mutation.asset_id and mutation.dispatch_id:
            return f'asset:{mutation.dispatch_id}:{mutation.asset_id}:{mutation.tool_call_id or ""}'
        if mutation.local_locator_key and mutation.dispatch_id:
            return f'local:{mutation.dispatch_id}:{mutation.tool_call_id or ""}:{mutation.local_locator_key}'
        if mutation.dispatch_id:
            return f'body:{mutation.dispatch_id}:{artifact_key}'
        if mutation.source_event_id:
            return f'event:{mutation.source_event_id}:{artifact_key}'
        return f'once:{uuid4().hex}'

    @staticmethod
    def _outbox_payload(mutation: ArtifactMutation, artifact_key: str) -> dict[str, object]:
        """生成可审计且不含正文或绝对路径的最小登记意图。"""
        return {
            'artifact_key': artifact_key,
            'artifact_kind': mutation.artifact_kind,
            'resource_uri': mutation.resource_uri,
            'source_kind': mutation.source_kind,
            'source_tool': mutation.source_tool,
            'source_app_id': mutation.source_app_id,
        }

    async def register(self, db: AsyncSession, mutation: ArtifactMutation) -> ArtifactRegistrationResult:
        """在同一事务写入当前态、参与记录和已确认的登记意图。"""
        artifact_key = self._artifact_key(mutation)
        contribution_key = self._contribution_idempotency_key(mutation, artifact_key)
        artifact_id = self._public_id('art')
        artifact_statement = (
            insert(HasnArtifacts)
            .values(
                artifact_id=artifact_id,
                owner_hasn_id=mutation.owner_hasn_id,
                artifact_key=artifact_key,
                artifact_kind=mutation.artifact_kind,
                # 兼容旧读路径期间同步该字段；参与上下文不再写入旧列。
                kind=mutation.artifact_kind,
                resource_kind=mutation.resource_kind,
                resource_app_id=mutation.resource_app_id,
                origin_ref=mutation.origin_ref,
                title=mutation.title,
                summary=mutation.summary,
                body=mutation.body,
                asset_id=mutation.asset_id,
                resource_uri=mutation.resource_uri,
                local_locator_key=mutation.local_locator_key,
                local_entry_kind=mutation.local_entry_kind,
                node_id=mutation.node_id,
                meta_data=mutation.metadata,
                status='active',
            )
            .on_conflict_do_update(
                index_elements=['owner_hasn_id', 'artifact_key'],
                set_={
                    'artifact_kind': mutation.artifact_kind,
                    'kind': mutation.artifact_kind,
                    'resource_kind': mutation.resource_kind,
                    'resource_app_id': mutation.resource_app_id,
                    'origin_ref': mutation.origin_ref,
                    'title': func.coalesce(mutation.title, HasnArtifacts.title),
                    'summary': func.coalesce(mutation.summary, HasnArtifacts.summary),
                    'body': mutation.body,
                    'asset_id': mutation.asset_id,
                    'resource_uri': mutation.resource_uri,
                    'local_locator_key': mutation.local_locator_key,
                    'local_entry_kind': mutation.local_entry_kind,
                    'node_id': mutation.node_id,
                    'metadata': mutation.metadata,
                    'updated_time': func.now(),
                },
            )
            .returning(HasnArtifacts.artifact_id)
        )
        canonical_artifact_id = (await db.execute(artifact_statement)).scalar_one()

        contribution_statement = (
            insert(HasnArtifactContributions)
            .values(
                contribution_id=self._public_id('con'),
                artifact_id=canonical_artifact_id,
                owner_hasn_id=mutation.owner_hasn_id,
                agent_hasn_id=mutation.agent_hasn_id,
                work_session_id=mutation.work_session_id,
                project_id=self._uuid_or_none(mutation.project_id),
                action=mutation.action,
                source_kind=mutation.source_kind,
                source_tool=mutation.source_tool,
                source_app_id=mutation.source_app_id,
                dispatch_id=mutation.dispatch_id,
                tool_call_id=mutation.tool_call_id,
                source_event_id=mutation.source_event_id,
                idempotency_key=contribution_key,
                conversation_id=self._uuid_or_none(mutation.conversation_id),
                message_id=mutation.message_id,
                meta_data=mutation.metadata,
            )
            .on_conflict_do_nothing(
                index_elements=['owner_hasn_id', 'agent_hasn_id', 'idempotency_key']
            )
            .returning(HasnArtifactContributions.contribution_id)
        )
        contribution_id = (await db.execute(contribution_statement)).scalar_one_or_none()
        if contribution_id is None:
            contribution_id = (
                await db.execute(
                    select(HasnArtifactContributions.contribution_id).where(
                        HasnArtifactContributions.owner_hasn_id == mutation.owner_hasn_id,
                        HasnArtifactContributions.agent_hasn_id == mutation.agent_hasn_id,
                        HasnArtifactContributions.idempotency_key == contribution_key,
                    )
                )
            ).scalar_one()

        outbox_key = f'{mutation.agent_hasn_id}:{contribution_key}'
        outbox_statement = insert(HasnArtifactRegistrationOutbox).values(
            outbox_id=self._public_id('aor'),
            owner_hasn_id=mutation.owner_hasn_id,
            artifact_id=canonical_artifact_id,
            idempotency_key=outbox_key,
            payload=self._outbox_payload(mutation, artifact_key),
            status='completed',
        )
        outbox_statement = outbox_statement.on_conflict_do_update(
            index_elements=['owner_hasn_id', 'idempotency_key'],
            set_={
                'artifact_id': canonical_artifact_id,
                'status': 'completed',
                'last_error': None,
                'lease_until': None,
                'updated_time': func.now(),
            },
        )
        await db.execute(outbox_statement)
        return ArtifactRegistrationResult(
            artifact_id=canonical_artifact_id,
            resource_uri=mutation.resource_uri,
        )


artifact_registration_service = ArtifactRegistrationService()
