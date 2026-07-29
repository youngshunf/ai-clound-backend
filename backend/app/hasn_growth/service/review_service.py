"""S11 经营复盘建议的创建、查询与 Owner 审阅。"""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation
from typing import Any
from uuid import UUID

import sqlalchemy as sa

from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.hasn_growth.model.growth_profile_version import GrowthProfileVersion
from backend.app.hasn_growth.model.growth_project import GrowthProject
from backend.app.hasn_growth.model.growth_review_suggestion import GrowthReviewSuggestion
from backend.app.hasn_growth.service.pii_boundary import assert_growth_pii_payload_safe
from backend.app.hasn_growth.service.playbook_service import playbook_service
from backend.common.exception import errors

_SUGGESTION_KINDS = frozenset({'icp', 'channel', 'playbook'})
_DECISIONS = frozenset({'accept', 'reject'})


def _parse_project_id(value: str | UUID) -> UUID:
    try:
        return value if isinstance(value, UUID) else UUID(str(value))
    except (TypeError, ValueError) as exc:
        raise errors.NotFoundError(msg='获客项目不存在') from exc


def _serialize(row: GrowthReviewSuggestion) -> dict[str, Any]:
    return {
        'id': row.id,
        'growth_project_id': str(row.growth_project_id),
        'suggestion_kind': row.suggestion_kind,
        'proposal': row.proposal,
        'evidence': row.evidence,
        'proposed_by_kind': row.proposed_by_kind,
        'proposed_by_id': row.proposed_by_id,
        'idempotency_key': row.idempotency_key,
        'status': row.status,
        'applied_version': row.applied_version,
        'reviewed_by_owner_id': row.reviewed_by_owner_id,
        'reviewed_time': row.reviewed_time.isoformat() if row.reviewed_time else None,
        'created_time': row.created_time.isoformat(),
        'updated_time': row.updated_time.isoformat() if row.updated_time else None,
    }


def _decimal_budget(value: Any) -> Decimal | None:
    if value is None:
        return None
    try:
        budget = Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError) as exc:
        raise errors.RequestError(msg='月度预算格式无效') from exc
    if budget < 0:
        raise errors.RequestError(msg='月度预算不能为负数')
    return budget


def _validate_evidence(evidence: dict[str, Any]) -> None:
    if not isinstance(evidence.get('scope'), str) or not evidence['scope'].strip():
        raise errors.RequestError(msg='建议必须说明证据范围')
    event_count = evidence.get('event_count')
    if not isinstance(event_count, int) or event_count < 0:
        raise errors.RequestError(msg='建议必须说明有效样本量')
    if not isinstance(evidence.get('insufficient_data'), bool):
        raise errors.RequestError(msg='建议必须明确数据是否充足')
    limitations = evidence.get('limitations')
    if (
        not isinstance(limitations, list)
        or not limitations
        or not all(isinstance(item, str) and item.strip() for item in limitations)
    ):
        raise errors.RequestError(msg='建议必须列出证据局限')
    if evidence.get('guaranteed_outcome') is True:
        raise errors.RequestError(msg='经营复盘建议不得承诺结果')


def _validate_channel_proposal(proposal: dict[str, Any]) -> None:
    start = proposal.get('quiet_hours_start')
    end = proposal.get('quiet_hours_end')
    limit = proposal.get('daily_outreach_limit')
    if not isinstance(start, int) or not 0 <= start <= 23:
        raise errors.RequestError(msg='静默时段开始小时无效')
    if not isinstance(end, int) or not 0 <= end <= 23 or end == start:
        raise errors.RequestError(msg='静默时段结束小时无效')
    if not isinstance(limit, int) or not 1 <= limit <= 10000:
        raise errors.RequestError(msg='每日触达上限无效')
    _decimal_budget(proposal.get('monthly_budget'))
    currency = proposal.get('budget_currency', 'CNY')
    if not isinstance(currency, str) or len(currency.strip()) != 3:
        raise errors.RequestError(msg='预算币种必须是三位代码')


class GrowthReviewService:
    """经营复盘建议只能先落待审记录，再由 Owner 接受或拒绝。"""

    @staticmethod
    async def _owned_project(
        db: AsyncSession,
        *,
        owner_hasn_id: str,
        growth_project_id: str | UUID,
        for_update: bool,
    ) -> GrowthProject:
        statement = sa.select(GrowthProject).where(
            GrowthProject.id == _parse_project_id(growth_project_id),
            GrowthProject.owner_hasn_id == owner_hasn_id,
        )
        if for_update:
            statement = statement.with_for_update()
        project = (await db.execute(statement)).scalar_one_or_none()
        if project is None:
            raise errors.NotFoundError(msg='获客项目不存在')
        return project

    async def create_suggestion(  # noqa: C901
        self,
        db: AsyncSession,
        *,
        owner_hasn_id: str,
        growth_project_id: str | UUID,
        suggestion_kind: str,
        proposal: dict[str, Any],
        evidence: dict[str, Any],
        proposed_by_kind: str,
        proposed_by_id: str,
        idempotency_key: str,
    ) -> dict[str, Any]:
        """幂等创建待审建议；同一键重放不同意图时显式冲突。"""
        if suggestion_kind not in _SUGGESTION_KINDS:
            raise errors.RequestError(msg='建议类型无效')
        if proposed_by_kind not in {'agent', 'system'} or not proposed_by_id.strip():
            raise errors.RequestError(msg='建议提出者无效')
        normalized_key = idempotency_key.strip()
        if not normalized_key or len(normalized_key) > 200:
            raise errors.RequestError(msg='建议幂等键无效')
        _validate_evidence(evidence)
        if suggestion_kind == 'channel':
            _validate_channel_proposal(proposal)
        elif suggestion_kind == 'playbook':
            if not isinstance(proposal.get('playbook_id'), int) or not isinstance(
                proposal.get('playbook_version'),
                int,
            ):
                raise errors.RequestError(msg='打法建议必须引用明确版本')
        elif not isinstance(proposal.get('icp_profile'), dict):
            raise errors.RequestError(msg='ICP 建议必须包含完整画像')
        assert_growth_pii_payload_safe({
            'proposal': proposal,
            'evidence': evidence,
            'proposed_by_id': proposed_by_id,
        })
        project = await self._owned_project(
            db,
            owner_hasn_id=owner_hasn_id,
            growth_project_id=growth_project_id,
            for_update=False,
        )
        existing = (
            await db.execute(
                sa.select(GrowthReviewSuggestion).where(
                    GrowthReviewSuggestion.growth_project_id == project.id,
                    GrowthReviewSuggestion.idempotency_key == normalized_key,
                )
            )
        ).scalar_one_or_none()
        if existing is not None:
            same_intent = (
                existing.suggestion_kind == suggestion_kind
                and existing.proposal == proposal
                and existing.evidence == evidence
                and existing.proposed_by_kind == proposed_by_kind
                and existing.proposed_by_id == proposed_by_id
            )
            if not same_intent:
                raise errors.ConflictError(
                    msg='建议幂等键已用于其他意图',
                    data={'error_code': 'GROWTH_REVIEW_SUGGESTION_CONFLICT'},
                )
            return _serialize(existing)
        statement = (
            pg_insert(GrowthReviewSuggestion)
            .values(
                growth_project_id=project.id,
                suggestion_kind=suggestion_kind,
                proposal=proposal,
                evidence=evidence,
                proposed_by_kind=proposed_by_kind,
                proposed_by_id=proposed_by_id,
                idempotency_key=normalized_key,
                status='pending',
            )
            .on_conflict_do_nothing(
                index_elements=[
                    GrowthReviewSuggestion.growth_project_id,
                    GrowthReviewSuggestion.idempotency_key,
                ]
            )
            .returning(GrowthReviewSuggestion.id)
        )
        suggestion_id = (await db.execute(statement)).scalar_one_or_none()
        if suggestion_id is None:
            row = (
                await db.execute(
                    sa.select(GrowthReviewSuggestion).where(
                        GrowthReviewSuggestion.growth_project_id == project.id,
                        GrowthReviewSuggestion.idempotency_key == normalized_key,
                    )
                )
            ).scalar_one()
            return _serialize(row)
        created_row = await db.get(GrowthReviewSuggestion, suggestion_id)
        if created_row is None:
            raise errors.ServerError(msg='经营复盘建议写入后无法读取')
        return _serialize(created_row)

    async def list_suggestions(
        self,
        db: AsyncSession,
        *,
        owner_hasn_id: str,
        growth_project_id: str | UUID,
    ) -> list[dict[str, Any]]:
        project = await self._owned_project(
            db,
            owner_hasn_id=owner_hasn_id,
            growth_project_id=growth_project_id,
            for_update=False,
        )
        rows = (
            (
                await db.execute(
                    sa
                    .select(GrowthReviewSuggestion)
                    .where(GrowthReviewSuggestion.growth_project_id == project.id)
                    .order_by(
                        GrowthReviewSuggestion.created_time.desc(),
                        GrowthReviewSuggestion.id.desc(),
                    )
                )
            )
            .scalars()
            .all()
        )
        return [_serialize(row) for row in rows]

    async def get_policy(
        self,
        db: AsyncSession,
        *,
        owner_hasn_id: str,
        growth_project_id: str | UUID,
    ) -> dict[str, Any]:
        """读取服务端实际执行的项目渠道策略。"""
        project = await self._owned_project(
            db,
            owner_hasn_id=owner_hasn_id,
            growth_project_id=growth_project_id,
            for_update=False,
        )
        return {
            'growth_project_id': str(project.id),
            'quiet_hours_start': project.quiet_hours_start,
            'quiet_hours_end': project.quiet_hours_end,
            'daily_outreach_limit': project.daily_outreach_limit,
            'monthly_budget': (str(project.monthly_budget) if project.monthly_budget is not None else None),
            'budget_currency': project.budget_currency,
            'policy_version': project.policy_version,
        }

    async def update_policy(
        self,
        db: AsyncSession,
        *,
        owner_hasn_id: str,
        growth_project_id: str | UUID,
        expected_policy_version: int,
        proposal: dict[str, Any],
    ) -> dict[str, Any]:
        """Owner 显式修改项目策略，使用乐观锁形成新版本。"""
        _validate_channel_proposal(proposal)
        project = await self._owned_project(
            db,
            owner_hasn_id=owner_hasn_id,
            growth_project_id=growth_project_id,
            for_update=True,
        )
        if project.status == 'archived':
            raise errors.ConflictError(
                msg='获客项目已归档，不能修改执行策略',
                data={'error_code': 'GROWTH_PROJECT_ARCHIVED'},
            )
        if project.policy_version != expected_policy_version:
            raise errors.ConflictError(
                msg='项目策略版本已变化，请刷新后重试',
                data={
                    'error_code': 'GROWTH_POLICY_VERSION_CONFLICT',
                    'current_version': project.policy_version,
                },
            )
        project.quiet_hours_start = proposal['quiet_hours_start']
        project.quiet_hours_end = proposal['quiet_hours_end']
        project.daily_outreach_limit = proposal['daily_outreach_limit']
        project.monthly_budget = _decimal_budget(proposal.get('monthly_budget'))
        project.budget_currency = str(proposal.get('budget_currency') or 'CNY').upper()
        project.policy_version += 1
        await db.flush()
        return await self.get_policy(
            db,
            owner_hasn_id=owner_hasn_id,
            growth_project_id=project.id,
        )

    async def review_suggestion(
        self,
        db: AsyncSession,
        *,
        owner_hasn_id: str,
        owner_user_id: int,
        growth_project_id: str | UUID,
        suggestion_id: int,
        decision: str,
    ) -> dict[str, Any]:
        """Owner 接受才应用并形成新版本；拒绝只改建议状态。"""
        if decision not in _DECISIONS:
            raise errors.RequestError(msg='decision 只允许 accept 或 reject')
        project = await self._owned_project(
            db,
            owner_hasn_id=owner_hasn_id,
            growth_project_id=growth_project_id,
            for_update=True,
        )
        suggestion = (
            await db.execute(
                sa
                .select(GrowthReviewSuggestion)
                .where(
                    GrowthReviewSuggestion.id == suggestion_id,
                    GrowthReviewSuggestion.growth_project_id == project.id,
                )
                .with_for_update()
            )
        ).scalar_one_or_none()
        if suggestion is None:
            raise errors.NotFoundError(msg='经营复盘建议不存在')
        expected_status = 'accepted' if decision == 'accept' else 'rejected'
        if suggestion.status == expected_status:
            return _serialize(suggestion)
        if suggestion.status != 'pending':
            raise errors.ConflictError(
                msg='经营复盘建议已处理',
                data={'error_code': 'GROWTH_REVIEW_SUGGESTION_REVIEWED'},
            )
        now = datetime.now(UTC)
        suggestion.reviewed_by_owner_id = str(owner_user_id)
        suggestion.reviewed_time = now
        if decision == 'reject':
            suggestion.status = 'rejected'
            await db.flush()
            return _serialize(suggestion)
        if project.status == 'archived':
            suggestion.status = 'stale'
            await db.flush()
            raise errors.ConflictError(
                msg='获客项目已归档，建议已失效',
                data={'error_code': 'GROWTH_PROJECT_ARCHIVED'},
            )
        if suggestion.suggestion_kind == 'channel':
            _validate_channel_proposal(suggestion.proposal)
            project.quiet_hours_start = suggestion.proposal['quiet_hours_start']
            project.quiet_hours_end = suggestion.proposal['quiet_hours_end']
            project.daily_outreach_limit = suggestion.proposal['daily_outreach_limit']
            project.monthly_budget = _decimal_budget(suggestion.proposal.get('monthly_budget'))
            project.budget_currency = str(suggestion.proposal.get('budget_currency') or 'CNY').upper()
            project.policy_version += 1
            suggestion.applied_version = project.policy_version
        elif suggestion.suggestion_kind == 'playbook':
            proposal = suggestion.proposal
            adoption = await playbook_service.adopt_for_project(
                db,
                owner_hasn_id=owner_hasn_id,
                user_id=owner_user_id,
                growth_project_id=project.id,
                playbook_id=int(proposal['playbook_id']),
                expected_playbook_version=int(proposal['playbook_version']),
                configuration=dict(proposal.get('configuration') or {}),
            )
            suggestion.applied_version = int(adoption['playbook_version'])
        else:
            icp_profile = suggestion.proposal['icp_profile']
            next_version = project.profile_version + 1
            current_version = (
                await db.execute(
                    sa.select(GrowthProfileVersion).where(
                        GrowthProfileVersion.growth_project_id == project.id,
                        GrowthProfileVersion.version == project.profile_version,
                    )
                )
            ).scalar_one_or_none()
            version = GrowthProfileVersion(
                growth_project_id=project.id,
                version=next_version,
                product_profile=project.product_profile,
                icp_profile=icp_profile,
                knowledge_document_versions=(current_version.knowledge_document_versions if current_version else []),
                source_hash=project.profile_source_hash or '',
                confirmed_by_kind='owner',
                confirmed_by_id=str(owner_user_id),
            )
            db.add(version)
            project.icp_profile = icp_profile
            project.profile_version = next_version
            project.profile_updated_time = now
            suggestion.applied_version = next_version
        suggestion.status = 'accepted'
        await db.flush()
        return _serialize(suggestion)


growth_review_service = GrowthReviewService()
