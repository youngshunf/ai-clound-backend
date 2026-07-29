"""会议增强候选 revision 的 owner 隔离事务服务。"""

from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.hasn_copilot.model import MeetingEnhancementRevisions, Meetings
from backend.common.exception import errors
from backend.utils.timezone import timezone

_PENDING = 'pending_confirmation'
_RETAINED_LIMIT = 5


def _to_uuid(value: str | UUID) -> UUID | None:
    if isinstance(value, UUID):
        return value
    try:
        return UUID(str(value))
    except (TypeError, ValueError):
        return None


def _revision_dict(revision: MeetingEnhancementRevisions) -> dict[str, Any]:
    """序列化候选；跨端只暴露云端 UUID，不生成任何本地 ID。"""
    return {
        'server_id': str(revision.id),
        'meeting_id': str(revision.meeting_id),
        'operation_id': revision.operation_id,
        'revision_number': revision.revision_number,
        'supersedes': str(revision.supersedes),
        'status': revision.status,
        'source_record_version': revision.source_record_version,
        'transcript': revision.transcript_json,
        'speaker_annotations': revision.speaker_annotations_json,
        'alignment': revision.alignment_json,
        'model_run_id': revision.model_run_id,
        'model_evidence': revision.model_evidence_json or {},
        'created_by_agent_hasn_id': revision.created_by_agent_hasn_id,
        'work_session_id': revision.work_session_id,
        'replaced_by': str(revision.replaced_by) if revision.replaced_by else None,
        'decision_reason': revision.decision_reason,
        'decided_time': revision.decided_time,
        'eviction_reason': revision.eviction_reason,
        'evicted_time': revision.evicted_time,
        'created_time': revision.created_time,
        'updated_time': revision.updated_time,
    }


class MeetingEnhancementRevisionsService:
    """候选创建、主人确认和保留策略的唯一业务入口。"""

    @staticmethod
    async def _get_owned_meeting(
        db: AsyncSession,
        *,
        owner_hasn_id: str,
        meeting_id: str | UUID,
        for_update: bool = False,
    ) -> Meetings:
        mid = _to_uuid(meeting_id)
        if mid is None:
            raise errors.NotFoundError(msg='会议不存在')
        query = select(Meetings).where(
            Meetings.id == mid,
            Meetings.owner_hasn_id == owner_hasn_id,
        )
        if for_update:
            query = query.with_for_update()
        meeting = (await db.execute(query)).scalar_one_or_none()
        if meeting is None:
            raise errors.NotFoundError(msg='会议不存在')
        return meeting

    @staticmethod
    async def _get_owned_revision(
        db: AsyncSession,
        *,
        meeting: Meetings,
        owner_hasn_id: str,
        revision_id: str | UUID,
    ) -> MeetingEnhancementRevisions:
        rid = _to_uuid(revision_id)
        if rid is None:
            raise errors.NotFoundError(msg='候选 revision 不存在')
        revision = (
            await db.execute(
                select(MeetingEnhancementRevisions).where(
                    MeetingEnhancementRevisions.id == rid,
                    MeetingEnhancementRevisions.meeting_id == meeting.id,
                    MeetingEnhancementRevisions.owner_hasn_id == owner_hasn_id,
                )
            )
        ).scalar_one_or_none()
        if revision is None:
            raise errors.NotFoundError(msg='候选 revision 不存在')
        return revision

    @staticmethod
    async def _validate_source(
        db: AsyncSession,
        *,
        meeting: Meetings,
        owner_hasn_id: str,
        supersedes: str | UUID,
    ) -> UUID:
        source_id = _to_uuid(supersedes)
        if source_id is None:
            raise errors.RequestError(msg='supersedes 不是有效的云端 revision ID')
        if source_id == meeting.realtime_revision_id:
            return source_id
        source = (
            await db.execute(
                select(MeetingEnhancementRevisions.id).where(
                    MeetingEnhancementRevisions.id == source_id,
                    MeetingEnhancementRevisions.meeting_id == meeting.id,
                    MeetingEnhancementRevisions.owner_hasn_id == owner_hasn_id,
                    MeetingEnhancementRevisions.status != 'evicted',
                )
            )
        ).scalar_one_or_none()
        if source is None:
            raise errors.RequestError(msg='supersedes 未指向本会议可用的来源 revision')
        return source_id

    @staticmethod
    def _assert_idempotent_payload(
        existing: MeetingEnhancementRevisions,
        *,
        source_id: UUID,
        source_record_version: int,
        transcript_json: dict | list,
        speaker_annotations_json: dict | list | None,
        alignment_json: dict | list | None,
        model_run_id: str | None,
        model_evidence_json: dict[str, Any],
    ) -> None:
        same = (
            existing.supersedes == source_id
            and existing.source_record_version == source_record_version
            and existing.transcript_json == transcript_json
            and existing.speaker_annotations_json == speaker_annotations_json
            and existing.alignment_json == alignment_json
            and existing.model_run_id == model_run_id
            and existing.model_evidence_json == model_evidence_json
        )
        if not same:
            raise errors.ConflictError(msg='同一 operation_id 的候选载荷不一致')

    @staticmethod
    async def _apply_retention(
        db: AsyncSession,
        *,
        meeting: Meetings,
        candidate: MeetingEnhancementRevisions,
        now: datetime,
    ) -> None:
        """最多保留五份候选正文；淘汰行继续承载不可变审计元数据。"""
        retained = (
            (
                await db.execute(
                    select(MeetingEnhancementRevisions)
                    .where(
                        MeetingEnhancementRevisions.meeting_id == meeting.id,
                        MeetingEnhancementRevisions.status != 'evicted',
                    )
                    .order_by(MeetingEnhancementRevisions.revision_number.asc())
                )
            )
            .scalars()
            .all()
        )
        overflow = len(retained) - _RETAINED_LIMIT
        if overflow <= 0:
            return
        preferred_id = meeting.preferred_enhancement_revision_id
        victims = [revision for revision in retained if revision.id != preferred_id and revision.id != candidate.id][
            :overflow
        ]
        if len(victims) != overflow:
            raise errors.ServerError(msg='会议候选保留策略无法安全淘汰当前首选 revision')
        for victim in victims:
            victim.status = 'evicted'
            victim.transcript_json = None
            victim.speaker_annotations_json = None
            victim.alignment_json = None
            victim.eviction_reason = 'retention_limit'
            victim.evicted_time = now
            victim.updated_time = now

    @staticmethod
    async def create_candidate(
        db: AsyncSession,
        *,
        owner_hasn_id: str,
        meeting_id: str | UUID,
        operation_id: str,
        supersedes: str | UUID,
        source_record_version: int,
        transcript_json: dict | list,
        speaker_annotations_json: dict | list | None = None,
        alignment_json: dict | list | None = None,
        model_run_id: str | None = None,
        model_evidence_json: dict[str, Any] | None = None,
        created_by_agent_hasn_id: str | None = None,
        work_session_id: str | None = None,
    ) -> dict[str, Any]:
        """在线性化会议行锁内创建候选、替换 pending 并执行五份保留策略。"""
        if not operation_id or len(operation_id) > 128:
            raise errors.RequestError(msg='operation_id 不能为空且不得超过 128 字符')
        if transcript_json is None:
            raise errors.RequestError(msg='候选 transcript 不能为空')
        if source_record_version < 0:
            raise errors.RequestError(msg='source_record_version 不能为负数')
        evidence = model_evidence_json or {}
        meeting = await MeetingEnhancementRevisionsService._get_owned_meeting(
            db,
            owner_hasn_id=owner_hasn_id,
            meeting_id=meeting_id,
            for_update=True,
        )
        if source_record_version > meeting.record_version:
            raise errors.RequestError(msg='候选不能引用尚不存在的原始实时稿版本')
        source_id = await MeetingEnhancementRevisionsService._validate_source(
            db,
            meeting=meeting,
            owner_hasn_id=owner_hasn_id,
            supersedes=supersedes,
        )

        existing = (
            await db.execute(
                select(MeetingEnhancementRevisions).where(
                    MeetingEnhancementRevisions.meeting_id == meeting.id,
                    MeetingEnhancementRevisions.operation_id == operation_id,
                )
            )
        ).scalar_one_or_none()
        if existing is not None:
            MeetingEnhancementRevisionsService._assert_idempotent_payload(
                existing,
                source_id=source_id,
                source_record_version=source_record_version,
                transcript_json=transcript_json,
                speaker_annotations_json=speaker_annotations_json,
                alignment_json=alignment_json,
                model_run_id=model_run_id,
                model_evidence_json=evidence,
            )
            return _revision_dict(existing)

        now = timezone.now()
        pending = (
            await db.execute(
                select(MeetingEnhancementRevisions).where(
                    MeetingEnhancementRevisions.meeting_id == meeting.id,
                    MeetingEnhancementRevisions.status == _PENDING,
                )
            )
        ).scalar_one_or_none()
        if pending is not None:
            pending.status = 'superseded'
            pending.decision_reason = 'replaced_by_new_candidate'
            pending.updated_time = now
            await db.flush()

        last_number = (
            await db.execute(
                select(func.max(MeetingEnhancementRevisions.revision_number)).where(
                    MeetingEnhancementRevisions.meeting_id == meeting.id
                )
            )
        ).scalar_one()
        candidate = MeetingEnhancementRevisions(
            meeting_id=meeting.id,
            owner_hasn_id=owner_hasn_id,
            operation_id=operation_id,
            revision_number=(last_number or 0) + 1,
            supersedes=source_id,
            status=_PENDING,
            source_record_version=source_record_version,
            transcript_json=transcript_json,
            speaker_annotations_json=speaker_annotations_json,
            alignment_json=alignment_json,
            model_run_id=model_run_id,
            model_evidence_json=evidence,
            created_by_agent_hasn_id=created_by_agent_hasn_id,
            work_session_id=work_session_id,
        )
        db.add(candidate)
        await db.flush()
        if pending is not None:
            pending.replaced_by = candidate.id

        await MeetingEnhancementRevisionsService._apply_retention(
            db,
            meeting=meeting,
            candidate=candidate,
            now=now,
        )
        await db.flush()
        return _revision_dict(candidate)

    @staticmethod
    async def accept_candidate(
        db: AsyncSession,
        *,
        owner_hasn_id: str,
        meeting_id: str | UUID,
        revision_id: str | UUID,
    ) -> dict[str, Any]:
        """主人接受候选后才切换首选视图，原始实时稿及其 ID 保持不变。"""
        meeting = await MeetingEnhancementRevisionsService._get_owned_meeting(
            db,
            owner_hasn_id=owner_hasn_id,
            meeting_id=meeting_id,
            for_update=True,
        )
        revision = await MeetingEnhancementRevisionsService._get_owned_revision(
            db,
            meeting=meeting,
            owner_hasn_id=owner_hasn_id,
            revision_id=revision_id,
        )
        if revision.status == 'evicted':
            raise errors.ConflictError(msg='已淘汰的候选不能接受')
        if revision.status == 'accepted' and meeting.preferred_enhancement_revision_id == revision.id:
            return _revision_dict(revision)

        now = timezone.now()
        current = (
            await db.execute(
                select(MeetingEnhancementRevisions).where(
                    MeetingEnhancementRevisions.meeting_id == meeting.id,
                    MeetingEnhancementRevisions.status == 'accepted',
                    MeetingEnhancementRevisions.id != revision.id,
                )
            )
        ).scalar_one_or_none()
        if current is not None:
            current.status = 'superseded'
            current.decision_reason = 'preferred_replaced'
            current.replaced_by = revision.id
            current.updated_time = now
        revision.status = 'accepted'
        revision.decision_reason = None
        revision.decided_time = now
        revision.updated_time = now
        meeting.preferred_enhancement_revision_id = revision.id
        meeting.updated_time = now
        await db.flush()
        return _revision_dict(revision)

    @staticmethod
    async def reject_candidate(
        db: AsyncSession,
        *,
        owner_hasn_id: str,
        meeting_id: str | UUID,
        revision_id: str | UUID,
        reason: str | None = None,
    ) -> dict[str, Any]:
        """主人拒绝当前待确认候选；首选视图不变。"""
        meeting = await MeetingEnhancementRevisionsService._get_owned_meeting(
            db,
            owner_hasn_id=owner_hasn_id,
            meeting_id=meeting_id,
            for_update=True,
        )
        revision = await MeetingEnhancementRevisionsService._get_owned_revision(
            db,
            meeting=meeting,
            owner_hasn_id=owner_hasn_id,
            revision_id=revision_id,
        )
        if revision.status != _PENDING:
            raise errors.ConflictError(msg='只有待确认候选可以拒绝')
        revision.status = 'rejected'
        revision.decision_reason = reason or 'owner_rejected'
        revision.decided_time = timezone.now()
        revision.updated_time = revision.decided_time
        await db.flush()
        return _revision_dict(revision)

    @staticmethod
    async def list_history(
        db: AsyncSession,
        *,
        owner_hasn_id: str,
        meeting_id: str | UUID,
    ) -> dict[str, Any]:
        meeting = await MeetingEnhancementRevisionsService._get_owned_meeting(
            db,
            owner_hasn_id=owner_hasn_id,
            meeting_id=meeting_id,
        )
        rows = (
            (
                await db.execute(
                    select(MeetingEnhancementRevisions)
                    .where(
                        MeetingEnhancementRevisions.meeting_id == meeting.id,
                        MeetingEnhancementRevisions.owner_hasn_id == owner_hasn_id,
                    )
                    .order_by(MeetingEnhancementRevisions.revision_number.desc())
                )
            )
            .scalars()
            .all()
        )
        items = [_revision_dict(row) for row in rows]
        return {
            'items': items,
            'pending_candidate': next(
                (item for item in items if item['status'] == _PENDING),
                None,
            ),
            'retained_count': sum(item['status'] != 'evicted' for item in items),
            'evicted_count': sum(item['status'] == 'evicted' for item in items),
        }

    @staticmethod
    async def get_revision_state(
        db: AsyncSession,
        *,
        meeting: Meetings,
        owner_hasn_id: str,
    ) -> dict[str, Any]:
        pending = (
            await db.execute(
                select(MeetingEnhancementRevisions).where(
                    MeetingEnhancementRevisions.meeting_id == meeting.id,
                    MeetingEnhancementRevisions.owner_hasn_id == owner_hasn_id,
                    MeetingEnhancementRevisions.status == _PENDING,
                )
            )
        ).scalar_one_or_none()
        if meeting.preferred_enhancement_revision_id is None:
            preferred_view = {
                'kind': 'original_realtime',
                'server_id': str(meeting.realtime_revision_id),
            }
        else:
            preferred = (
                await db.execute(
                    select(MeetingEnhancementRevisions.id).where(
                        MeetingEnhancementRevisions.id == meeting.preferred_enhancement_revision_id,
                        MeetingEnhancementRevisions.meeting_id == meeting.id,
                        MeetingEnhancementRevisions.owner_hasn_id == owner_hasn_id,
                        MeetingEnhancementRevisions.status == 'accepted',
                    )
                )
            ).scalar_one_or_none()
            if preferred is None:
                raise errors.ServerError(msg='会议首选增强 revision 指针已损坏')
            preferred_view = {
                'kind': 'enhancement',
                'server_id': str(preferred),
            }
        return {
            'original_realtime': {
                'server_id': str(meeting.realtime_revision_id),
                'record_version': meeting.record_version,
            },
            'preferred_view': preferred_view,
            'pending_candidate': _revision_dict(pending) if pending else None,
        }


meeting_enhancement_revisions_service = MeetingEnhancementRevisionsService()
