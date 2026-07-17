"""会议副驾 v5 云端会议结果域 meetings_service 真实 PG 测试（零 mock）。

覆盖 daemon 契约主链（设计事实源 §6.0.7）：
- create（按 session_id upsert，UUID 权威 id）→ get detail（meeting/segments/minutes/relation/my_permission）
  → patch（改字段 + 非法值拒绝）→ put segments（幂等上推 + bump record_version）
  → write minutes（幂等 version + minutes_state=ready）→ list → media 升格/撤销 → 分享/撤销 → delete。
- owner 硬隔离（A 取不到 / 改不到 / 列不到 B 的会议）。
- 会议对象序列化字段名（daemon meetings_mirror::from_cloud 精确读取）。

用真实本地 PostgreSQL（不可达则 skip）：插入隔离测试行 → flush（不 commit）→ 断言 → rollback。
"""

from __future__ import annotations

import uuid

import pytest
import pytest_asyncio

from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from backend.app.hasn.model import HasnAgents
from backend.app.hasn_copilot.model import MeetingMinutes, MeetingTranscriptSegments, Meetings
from backend.app.hasn_copilot.service.meetings_service import meetings_service
from backend.common.exception import errors
from backend.database.db import SQLALCHEMY_DATABASE_URL

pytestmark = pytest.mark.asyncio


@pytest_asyncio.fixture
async def session():
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


async def _seed_agent(session, *, owner_id: str, tag: str) -> str:
    """为 owner 插入一个名下分身，返回 agent hasn_id（star_id 全局 UNIQUE，须唯一）。"""
    agent_id = f'a_{tag}'
    session.add(HasnAgents(hasn_id=agent_id, owner_id=owner_id, star_id=f'star_{tag}', display_name=f'分身-{tag}'))
    await session.flush()
    return agent_id


# ============================ 起会 / 序列化 ============================


async def test_create_returns_uuid_id_and_contract_fields(session) -> None:
    tag = uuid.uuid4().hex[:8]
    owner = f'h_owner_{tag}'
    data = await meetings_service.create_meeting(
        session, owner_hasn_id=owner, session_id=f'sess_{tag}', title='周会', scene='meeting', started_at=1000
    )
    # 权威 id 必须是 UUID 字符串（hasn://meeting/{id} 的 {id} 段）
    uuid.UUID(data['id'])
    assert data['owner_hasn_id'] == owner
    assert data['title'] == '周会'
    assert data['scene'] == 'meeting'
    assert data['status'] == 'active'
    assert data['record_version'] == 0
    assert data['minutes_state'] == 'none'
    assert data['minutes_version'] == 0
    # daemon meetings_mirror 精确读取的数组/对象/整数字段
    assert data['participants'] == []
    assert data['shared_media'] == []
    assert data['stats'] == {}
    assert data['started_at'] == 1000  # 整数（unix 秒）
    # 契约要求的键齐全
    for key in ('id', 'agent_hasn_id', 'session_id', 'node_id', 'ended_at', 'duration_ms'):
        assert key in data


async def test_create_idempotent_by_session(session) -> None:
    tag = uuid.uuid4().hex[:8]
    owner = f'h_owner_{tag}'
    sid = f'sess_{tag}'
    d1 = await meetings_service.create_meeting(session, owner_hasn_id=owner, session_id=sid, title='一')
    d2 = await meetings_service.create_meeting(session, owner_hasn_id=owner, session_id=sid, title='二')
    assert d1['id'] == d2['id']  # 已存在返回既有行，不重复建
    rows = (
        (await session.execute(select(Meetings).where(Meetings.owner_hasn_id == owner, Meetings.session_id == sid)))
        .scalars()
        .all()
    )
    assert len(rows) == 1


async def test_create_foreign_agent_rejected(session) -> None:
    tag = uuid.uuid4().hex[:8]
    owner_a = f'h_owner_a_{tag}'
    owner_b = f'h_owner_b_{tag}'
    agent_b = await _seed_agent(session, owner_id=owner_b, tag=f'b_{tag}')
    with pytest.raises(errors.NotFoundError):
        await meetings_service.create_meeting(
            session, owner_hasn_id=owner_a, session_id=f'sess_{tag}', agent_hasn_id=agent_b
        )


# ============================ 详情 / 改字段 ============================


async def test_get_detail_shape(session) -> None:
    tag = uuid.uuid4().hex[:8]
    owner = f'h_owner_{tag}'
    created = await meetings_service.create_meeting(session, owner_hasn_id=owner, session_id=f'sess_{tag}')
    detail = await meetings_service.get_detail(session, owner_hasn_id=owner, meeting_id=created['id'])
    assert detail['meeting']['id'] == created['id']
    assert detail['segments'] == []
    assert detail['minutes'] == []
    assert detail['relation'] == 'owner'
    assert detail['my_permission'] == 'manage'


async def test_get_detail_bad_uuid_is_404(session) -> None:
    tag = uuid.uuid4().hex[:8]
    with pytest.raises(errors.NotFoundError):
        await meetings_service.get_detail(session, owner_hasn_id=f'h_{tag}', meeting_id='not-a-uuid')


async def test_patch_updates_and_rejects_invalid(session) -> None:
    tag = uuid.uuid4().hex[:8]
    owner = f'h_owner_{tag}'
    created = await meetings_service.create_meeting(session, owner_hasn_id=owner, session_id=f'sess_{tag}')
    mid = created['id']
    updated = await meetings_service.patch_meeting(
        session,
        owner_hasn_id=owner,
        meeting_id=mid,
        patch={
            'title': '改后',
            'status': 'ended',
            'ended_at': 2000,
            'duration_ms': 1000,
            'participants_json': [{'cluster_id': 'c1', 'speaker_label': '说话人1'}],
            'stats_json': {'segments': 3},
        },
    )
    assert updated['title'] == '改后'
    assert updated['status'] == 'ended'
    assert updated['ended_at'] == 2000
    assert updated['duration_ms'] == 1000
    assert updated['participants'][0]['speaker_label'] == '说话人1'
    assert updated['stats'] == {'segments': 3}
    # 非法场景 / 状态被拒
    with pytest.raises(errors.RequestError):
        await meetings_service.patch_meeting(session, owner_hasn_id=owner, meeting_id=mid, patch={'scene': 'party'})
    with pytest.raises(errors.RequestError):
        await meetings_service.patch_meeting(session, owner_hasn_id=owner, meeting_id=mid, patch={'status': 'bogus'})


# ============================ 转写定稿 / 纪要 ============================


async def test_put_segments_bumps_and_upserts(session) -> None:
    tag = uuid.uuid4().hex[:8]
    owner = f'h_owner_{tag}'
    created = await meetings_service.create_meeting(session, owner_hasn_id=owner, session_id=f'sess_{tag}')
    mid = created['id']
    res = await meetings_service.put_segments(
        session,
        owner_hasn_id=owner,
        meeting_id=mid,
        record_version=1,
        segments=[
            {'seq': 0, 'track': 'mic', 'speaker_label': 'S1', 'text': '你好', 'started_ms': 0, 'ended_ms': 500},
            {'seq': 1, 'track': 'system', 'text': '在的', 'started_ms': 500, 'ended_ms': 900},
        ],
    )
    assert res['meeting_id'] == mid
    assert res['record_version'] == 1
    assert res['segment_count'] == 2
    detail = await meetings_service.get_detail(session, owner_hasn_id=owner, meeting_id=mid)
    assert detail['meeting']['record_version'] == 1
    assert len(detail['segments']) == 2
    assert detail['segments'][0]['seq'] == 0
    assert detail['segments'][0]['text'] == '你好'

    # 同 record_version 同 seq 再推 = upsert（不产生重复行）
    await meetings_service.put_segments(
        session,
        owner_hasn_id=owner,
        meeting_id=mid,
        record_version=1,
        segments=[{'seq': 0, 'track': 'mic', 'text': '你好（修订）', 'started_ms': 0, 'ended_ms': 500}],
    )
    seg_rows = (
        (
            await session.execute(
                select(MeetingTranscriptSegments).where(
                    MeetingTranscriptSegments.meeting_id == uuid.UUID(mid),
                    MeetingTranscriptSegments.record_version == 1,
                    MeetingTranscriptSegments.seq == 0,
                )
            )
        )
        .scalars()
        .all()
    )
    assert len(seg_rows) == 1
    assert seg_rows[0].text == '你好（修订）'

    # 新 record_version → bump 到 2，详情按当前版本过滤
    await meetings_service.put_segments(
        session,
        owner_hasn_id=owner,
        meeting_id=mid,
        record_version=2,
        segments=[{'seq': 0, 'text': '第二版', 'started_ms': 0}],
    )
    detail2 = await meetings_service.get_detail(session, owner_hasn_id=owner, meeting_id=mid)
    assert detail2['meeting']['record_version'] == 2
    assert len(detail2['segments']) == 1
    assert detail2['segments'][0]['text'] == '第二版'


async def test_write_minutes_sets_ready_and_versions(session) -> None:
    tag = uuid.uuid4().hex[:8]
    owner = f'h_owner_{tag}'
    created = await meetings_service.create_meeting(session, owner_hasn_id=owner, session_id=f'sess_{tag}')
    mid = created['id']
    m1 = await meetings_service.write_minutes(
        session, owner_hasn_id=owner, meeting_id=mid, version=1, body_md='# 纪要 v1', record_view_version=1
    )
    assert m1['minutes_state'] == 'ready'
    assert m1['minutes_version'] == 1

    # 同 version 重写 = upsert（不新增版本行）
    await meetings_service.write_minutes(
        session, owner_hasn_id=owner, meeting_id=mid, version=1, body_md='# 纪要 v1 改'
    )
    rows = (
        (await session.execute(select(MeetingMinutes).where(MeetingMinutes.meeting_id == uuid.UUID(mid))))
        .scalars()
        .all()
    )
    assert len(rows) == 1
    assert rows[0].body_md == '# 纪要 v1 改'

    # 新 version → minutes_version 提升，详情含两版
    await meetings_service.write_minutes(session, owner_hasn_id=owner, meeting_id=mid, version=2, body_md='# 纪要 v2')
    detail = await meetings_service.get_detail(session, owner_hasn_id=owner, meeting_id=mid)
    assert detail['meeting']['minutes_version'] == 2
    assert len(detail['minutes']) == 2
    assert detail['minutes'][0]['version'] == 2  # 按 version DESC


# ============================ 升格媒体 ============================


async def test_media_add_replace_delete(session) -> None:
    tag = uuid.uuid4().hex[:8]
    owner = f'h_owner_{tag}'
    created = await meetings_service.create_meeting(session, owner_hasn_id=owner, session_id=f'sess_{tag}')
    mid = created['id']
    d1 = await meetings_service.add_media(
        session,
        owner_hasn_id=owner,
        meeting_id=mid,
        media={'kind': 'audio', 'sha256': 'abc', 'asset_uri': 'hasn://asset/a1'},
    )
    assert len(d1['shared_media']) == 1
    media_id = d1['shared_media'][0]['media_id']
    assert media_id  # 无 media_id 时云端生成

    # 同 sha256+kind 再加 = 替换（不新增），media_id 稳定
    d2 = await meetings_service.add_media(
        session,
        owner_hasn_id=owner,
        meeting_id=mid,
        media={'kind': 'audio', 'sha256': 'abc', 'asset_uri': 'hasn://asset/a2'},
    )
    assert len(d2['shared_media']) == 1
    assert d2['shared_media'][0]['media_id'] == media_id
    assert d2['shared_media'][0]['asset_uri'] == 'hasn://asset/a2'

    # 不同 sha256 → 追加
    d3 = await meetings_service.add_media(
        session, owner_hasn_id=owner, meeting_id=mid, media={'kind': 'video', 'sha256': 'def'}
    )
    assert len(d3['shared_media']) == 2

    # 删除首件
    d4 = await meetings_service.delete_media(session, owner_hasn_id=owner, meeting_id=mid, media_id=media_id)
    ids = {item['media_id'] for item in d4['shared_media']}
    assert media_id not in ids
    assert len(d4['shared_media']) == 1


async def test_media_requires_sha256_and_kind(session) -> None:
    tag = uuid.uuid4().hex[:8]
    owner = f'h_owner_{tag}'
    created = await meetings_service.create_meeting(session, owner_hasn_id=owner, session_id=f'sess_{tag}')
    with pytest.raises(errors.RequestError):
        await meetings_service.add_media(
            session, owner_hasn_id=owner, meeting_id=created['id'], media={'kind': 'audio'}
        )


# ============================ 分享 / 撤销 ============================


async def test_share_and_revoke(session) -> None:
    tag = uuid.uuid4().hex[:8]
    owner = f'h_owner_{tag}'
    grantee = f'h_grantee_{tag}'
    created = await meetings_service.create_meeting(session, owner_hasn_id=owner, session_id=f'sess_{tag}')
    mid = created['id']
    shared = await meetings_service.share_meeting(session, owner_hasn_id=owner, meeting_id=mid, grantee_hasn_id=grantee)
    assert shared['shared'] is True
    assert shared['grantee_hasn_id'] == grantee
    assert shared['permission'] == 'viewer'  # 默认 view → 内部 viewer

    revoked = await meetings_service.share_revoke(session, owner_hasn_id=owner, meeting_id=mid, grantee_hasn_id=grantee)
    assert revoked['revoked'] is True
    # 再撤 = 无命中
    revoked2 = await meetings_service.share_revoke(
        session, owner_hasn_id=owner, meeting_id=mid, grantee_hasn_id=grantee
    )
    assert revoked2['revoked'] is False


async def test_share_invalid_permission_rejected(session) -> None:
    tag = uuid.uuid4().hex[:8]
    owner = f'h_owner_{tag}'
    created = await meetings_service.create_meeting(session, owner_hasn_id=owner, session_id=f'sess_{tag}')
    with pytest.raises(errors.RequestError):
        await meetings_service.share_meeting(
            session, owner_hasn_id=owner, meeting_id=created['id'], grantee_hasn_id=f'h_g_{tag}', permission='god'
        )


# ============================ 删除 ============================


async def test_delete_all_cascades(session) -> None:
    tag = uuid.uuid4().hex[:8]
    owner = f'h_owner_{tag}'
    created = await meetings_service.create_meeting(session, owner_hasn_id=owner, session_id=f'sess_{tag}')
    mid = created['id']
    await meetings_service.put_segments(
        session,
        owner_hasn_id=owner,
        meeting_id=mid,
        record_version=1,
        segments=[{'seq': 0, 'text': 'x', 'started_ms': 0}],
    )
    await meetings_service.write_minutes(session, owner_hasn_id=owner, meeting_id=mid, version=1, body_md='m')
    res = await meetings_service.delete_meeting(session, owner_hasn_id=owner, meeting_id=mid, scope='all')
    assert res == {'deleted': True, 'scope': 'all'}
    with pytest.raises(errors.NotFoundError):
        await meetings_service.get_detail(session, owner_hasn_id=owner, meeting_id=mid)
    seg = (
        (
            await session.execute(
                select(MeetingTranscriptSegments).where(MeetingTranscriptSegments.meeting_id == uuid.UUID(mid))
            )
        )
        .scalars()
        .all()
    )
    minutes = (
        (await session.execute(select(MeetingMinutes).where(MeetingMinutes.meeting_id == uuid.UUID(mid))))
        .scalars()
        .all()
    )
    assert seg == []
    assert minutes == []


async def test_delete_local_media_keeps_meeting(session) -> None:
    tag = uuid.uuid4().hex[:8]
    owner = f'h_owner_{tag}'
    created = await meetings_service.create_meeting(session, owner_hasn_id=owner, session_id=f'sess_{tag}')
    mid = created['id']
    res = await meetings_service.delete_meeting(session, owner_hasn_id=owner, meeting_id=mid, scope='local_media')
    assert res == {'deleted': True, 'scope': 'local_media'}
    # 云端结果保留
    detail = await meetings_service.get_detail(session, owner_hasn_id=owner, meeting_id=mid)
    assert detail['meeting']['id'] == mid


async def test_delete_invalid_scope_rejected(session) -> None:
    tag = uuid.uuid4().hex[:8]
    owner = f'h_owner_{tag}'
    created = await meetings_service.create_meeting(session, owner_hasn_id=owner, session_id=f'sess_{tag}')
    with pytest.raises(errors.RequestError):
        await meetings_service.delete_meeting(
            session, owner_hasn_id=owner, meeting_id=created['id'], scope='everything'
        )


# ============================ owner 硬隔离 ============================


async def test_owner_isolation(session) -> None:
    tag = uuid.uuid4().hex[:8]
    owner_a = f'h_owner_a_{tag}'
    owner_b = f'h_owner_b_{tag}'
    b = await meetings_service.create_meeting(
        session, owner_hasn_id=owner_b, session_id=f'sess_b_{tag}', title='B 的会议'
    )
    mid_b = b['id']

    # A 取不到 B 的会议详情
    with pytest.raises(errors.NotFoundError):
        await meetings_service.get_detail(session, owner_hasn_id=owner_a, meeting_id=mid_b)
    # A 改不动 B 的会议
    with pytest.raises(errors.NotFoundError):
        await meetings_service.patch_meeting(session, owner_hasn_id=owner_a, meeting_id=mid_b, patch={'title': 'x'})
    # A 不能往 B 的会议推 segments / 删除
    with pytest.raises(errors.NotFoundError):
        await meetings_service.put_segments(
            session, owner_hasn_id=owner_a, meeting_id=mid_b, record_version=1, segments=[]
        )
    with pytest.raises(errors.NotFoundError):
        await meetings_service.delete_meeting(session, owner_hasn_id=owner_a, meeting_id=mid_b, scope='all')

    # A 的列表看不到 B 的会议；B 自己看得到
    listing_a = await meetings_service.list_meetings(session, owner_hasn_id=owner_a)
    assert mid_b not in {item['id'] for item in listing_a['items']}
    listing_b = await meetings_service.list_meetings(session, owner_hasn_id=owner_b)
    assert mid_b in {item['id'] for item in listing_b['items']}
    assert listing_b['total'] >= 1
