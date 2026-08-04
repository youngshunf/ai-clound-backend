"""会议增强候选 revision 服务的真实 PostgreSQL 测试。"""

from __future__ import annotations

import asyncio
import uuid

from collections.abc import AsyncGenerator

import pytest
import pytest_asyncio

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from backend.app.hasn_copilot.model import MeetingEnhancementRevisions, Meetings
from backend.app.hasn_copilot.service.meeting_enhancement_revisions_service import (
    meeting_enhancement_revisions_service,
)
from backend.app.hasn_copilot.service.meetings_service import meetings_service
from backend.common.exception import errors
from backend.database.db import SQLALCHEMY_DATABASE_URL

pytestmark = pytest.mark.asyncio


@pytest_asyncio.fixture
async def session() -> AsyncGenerator[AsyncSession, None]:
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


def _candidate_payload(*, operation_id: str, supersedes: str, text: str) -> dict:
    return {
        'operation_id': operation_id,
        'supersedes': supersedes,
        'source_record_version': 0,
        'transcript_json': {'text': text, 'segments': []},
        'speaker_annotations_json': [],
        'alignment_json': {'segments': []},
        'model_run_id': f'run-{operation_id}',
        'model_evidence_json': {
            'component_id': 'moss-transcribe-diarize-0.9b',
            'component_revision': 'fixed-revision',
            'capabilities': ['transcription', 'speaker_diarization', 'segment_timestamps'],
        },
    }


async def test_candidate_keeps_original_realtime_as_default_view(session: AsyncSession) -> None:
    tag = uuid.uuid4().hex[:8]
    owner = f'h_owner_{tag}'
    meeting = await meetings_service.create_meeting(
        session,
        owner_hasn_id=owner,
        session_id=f'session_{tag}',
    )
    realtime_revision_id = meeting['realtime_revision_id']

    candidate = await meeting_enhancement_revisions_service.create_candidate(
        session,
        owner_hasn_id=owner,
        meeting_id=meeting['id'],
        **_candidate_payload(
            operation_id=f'op_{tag}',
            supersedes=realtime_revision_id,
            text='增强候选',
        ),
    )
    assert candidate['server_id'] != realtime_revision_id
    assert candidate['status'] == 'pending_confirmation'
    assert candidate['supersedes'] == realtime_revision_id

    detail = await meetings_service.get_detail(
        session,
        owner_hasn_id=owner,
        meeting_id=meeting['id'],
    )
    assert detail['revision_state']['preferred_view'] == {
        'kind': 'original_realtime',
        'server_id': realtime_revision_id,
    }
    assert detail['revision_state']['pending_candidate']['server_id'] == candidate['server_id']


async def test_new_candidate_replaces_pending_and_keeps_history(session: AsyncSession) -> None:
    tag = uuid.uuid4().hex[:8]
    owner = f'h_owner_{tag}'
    meeting = await meetings_service.create_meeting(
        session,
        owner_hasn_id=owner,
        session_id=f'session_{tag}',
    )
    source = meeting['realtime_revision_id']
    first = await meeting_enhancement_revisions_service.create_candidate(
        session,
        owner_hasn_id=owner,
        meeting_id=meeting['id'],
        **_candidate_payload(operation_id=f'op_1_{tag}', supersedes=source, text='第一版'),
    )
    second = await meeting_enhancement_revisions_service.create_candidate(
        session,
        owner_hasn_id=owner,
        meeting_id=meeting['id'],
        **_candidate_payload(operation_id=f'op_2_{tag}', supersedes=source, text='第二版'),
    )

    history = await meeting_enhancement_revisions_service.list_history(
        session,
        owner_hasn_id=owner,
        meeting_id=meeting['id'],
    )
    by_id = {item['server_id']: item for item in history['items']}
    assert by_id[first['server_id']]['status'] == 'superseded'
    assert by_id[first['server_id']]['replaced_by'] == second['server_id']
    assert by_id[second['server_id']]['status'] == 'pending_confirmation'
    assert history['pending_candidate']['server_id'] == second['server_id']


async def test_operation_replay_is_idempotent_and_conflict_is_explicit(session: AsyncSession) -> None:
    tag = uuid.uuid4().hex[:8]
    owner = f'h_owner_{tag}'
    meeting = await meetings_service.create_meeting(
        session,
        owner_hasn_id=owner,
        session_id=f'session_{tag}',
    )
    payload = _candidate_payload(
        operation_id=f'op_{tag}',
        supersedes=meeting['realtime_revision_id'],
        text='幂等候选',
    )
    first = await meeting_enhancement_revisions_service.create_candidate(
        session,
        owner_hasn_id=owner,
        meeting_id=meeting['id'],
        **payload,
    )
    replay = await meeting_enhancement_revisions_service.create_candidate(
        session,
        owner_hasn_id=owner,
        meeting_id=meeting['id'],
        **payload,
    )
    assert replay['server_id'] == first['server_id']

    conflicting = dict(payload)
    conflicting['transcript_json'] = {'text': '冲突载荷'}
    with pytest.raises(errors.ConflictError):
        await meeting_enhancement_revisions_service.create_candidate(
            session,
            owner_hasn_id=owner,
            meeting_id=meeting['id'],
            **conflicting,
        )
    rows = (
        (
            await session.execute(
                select(MeetingEnhancementRevisions).where(
                    MeetingEnhancementRevisions.meeting_id == uuid.UUID(meeting['id'])
                )
            )
        )
        .scalars()
        .all()
    )
    assert len(rows) == 1


async def test_accept_switches_preferred_view_and_reject_keeps_it(session: AsyncSession) -> None:
    tag = uuid.uuid4().hex[:8]
    owner = f'h_owner_{tag}'
    meeting = await meetings_service.create_meeting(
        session,
        owner_hasn_id=owner,
        session_id=f'session_{tag}',
    )
    source = meeting['realtime_revision_id']
    first = await meeting_enhancement_revisions_service.create_candidate(
        session,
        owner_hasn_id=owner,
        meeting_id=meeting['id'],
        **_candidate_payload(operation_id=f'op_1_{tag}', supersedes=source, text='接受版'),
    )
    accepted = await meeting_enhancement_revisions_service.accept_candidate(
        session,
        owner_hasn_id=owner,
        meeting_id=meeting['id'],
        revision_id=first['server_id'],
    )
    assert accepted['status'] == 'accepted'

    second = await meeting_enhancement_revisions_service.create_candidate(
        session,
        owner_hasn_id=owner,
        meeting_id=meeting['id'],
        **_candidate_payload(
            operation_id=f'op_2_{tag}',
            supersedes=first['server_id'],
            text='拒绝版',
        ),
    )
    await meeting_enhancement_revisions_service.reject_candidate(
        session,
        owner_hasn_id=owner,
        meeting_id=meeting['id'],
        revision_id=second['server_id'],
        reason='owner_rejected',
    )
    detail = await meetings_service.get_detail(
        session,
        owner_hasn_id=owner,
        meeting_id=meeting['id'],
    )
    assert detail['revision_state']['preferred_view'] == {
        'kind': 'enhancement',
        'server_id': first['server_id'],
    }
    assert detail['revision_state']['pending_candidate'] is None


async def test_sixth_candidate_evicts_only_oldest_and_keeps_audit(session: AsyncSession) -> None:
    tag = uuid.uuid4().hex[:8]
    owner = f'h_owner_{tag}'
    meeting = await meetings_service.create_meeting(
        session,
        owner_hasn_id=owner,
        session_id=f'session_{tag}',
    )
    source = meeting['realtime_revision_id']
    created: list[dict] = [
        await meeting_enhancement_revisions_service.create_candidate(
            session,
            owner_hasn_id=owner,
            meeting_id=meeting['id'],
            **_candidate_payload(
                operation_id=f'op_{index}_{tag}',
                supersedes=source,
                text=f'候选 {index}',
            ),
        )
        for index in range(6)
    ]

    history = await meeting_enhancement_revisions_service.list_history(
        session,
        owner_hasn_id=owner,
        meeting_id=meeting['id'],
    )
    assert history['retained_count'] == 5
    assert history['evicted_count'] == 1
    oldest = next(item for item in history['items'] if item['server_id'] == created[0]['server_id'])
    assert oldest['status'] == 'evicted'
    assert oldest['eviction_reason'] == 'retention_limit'
    assert oldest['transcript'] is None
    assert sum(item['status'] == 'evicted' for item in history['items']) == 1


async def test_cross_owner_candidate_access_returns_non_leaking_404(session: AsyncSession) -> None:
    tag = uuid.uuid4().hex[:8]
    owner_a = f'h_owner_a_{tag}'
    owner_b = f'h_owner_b_{tag}'
    meeting = await meetings_service.create_meeting(
        session,
        owner_hasn_id=owner_a,
        session_id=f'session_{tag}',
    )
    candidate = await meeting_enhancement_revisions_service.create_candidate(
        session,
        owner_hasn_id=owner_a,
        meeting_id=meeting['id'],
        **_candidate_payload(
            operation_id=f'op_{tag}',
            supersedes=meeting['realtime_revision_id'],
            text='私有候选',
        ),
    )
    with pytest.raises(errors.NotFoundError):
        await meeting_enhancement_revisions_service.accept_candidate(
            session,
            owner_hasn_id=owner_b,
            meeting_id=meeting['id'],
            revision_id=candidate['server_id'],
        )


async def test_concurrent_creation_serializes_and_keeps_highest_sequence_pending() -> None:
    engine = create_async_engine(SQLALCHEMY_DATABASE_URL, poolclass=NullPool)
    maker = async_sessionmaker(engine, expire_on_commit=False)
    tag = uuid.uuid4().hex[:8]
    owner = f'h_owner_{tag}'
    meeting_id: str | None = None
    try:
        async with engine.connect() as conn:
            await conn.execute(select(1))
        async with maker.begin() as setup:
            meeting = await meetings_service.create_meeting(
                setup,
                owner_hasn_id=owner,
                session_id=f'session_{tag}',
            )
            meeting_id = meeting['id']
            source = meeting['realtime_revision_id']

        async def create(index: int) -> dict:
            async with maker.begin() as db:
                return await meeting_enhancement_revisions_service.create_candidate(
                    db,
                    owner_hasn_id=owner,
                    meeting_id=meeting['id'],
                    **_candidate_payload(
                        operation_id=f'op_{index}_{tag}',
                        supersedes=source,
                        text=f'并发候选 {index}',
                    ),
                )

        await asyncio.gather(create(1), create(2))
        async with maker() as verify:
            rows = (
                (
                    await verify.execute(
                        select(MeetingEnhancementRevisions)
                        .where(MeetingEnhancementRevisions.meeting_id == uuid.UUID(meeting['id']))
                        .order_by(MeetingEnhancementRevisions.revision_number)
                    )
                )
                .scalars()
                .all()
            )
            assert [row.revision_number for row in rows] == [1, 2]
            assert sum(row.status == 'pending_confirmation' for row in rows) == 1
            assert rows[-1].status == 'pending_confirmation'
            assert rows[0].status == 'superseded'
    except Exception as exc:
        if meeting_id is None:
            pytest.skip(f'本地 PostgreSQL 不可达，跳过: {exc!r}')
        raise
    finally:
        if meeting_id is not None:
            async with maker.begin() as cleanup:
                mid = uuid.UUID(meeting_id)
                await cleanup.execute(
                    delete(MeetingEnhancementRevisions).where(MeetingEnhancementRevisions.meeting_id == mid)
                )
                await cleanup.execute(delete(Meetings).where(Meetings.id == mid))
        await engine.dispose()
