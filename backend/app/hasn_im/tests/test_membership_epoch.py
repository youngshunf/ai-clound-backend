"""R2-03 验收：成员周期 epoch + 已读/未读（真实 PG·零 mock·doc16 §4.2/§4.3）。

钉死四条验收（+域纯函数 + 唯一约束兜底）：
1. **退出/重入**：leave 闭合 left_seq 不删行；rejoin 建新行、新 joined_seq，两段周期并存。
2. **两段可见区间**：加入前 / 离开期间的消息不可见；跨两段周期的可见并集恰为两段区间。
3. **read_seq 单调 + clamp**：只进不退、clamp 到本周期可见上界。
4. **唯一约束**：同 (会话, 成员) 两个活动周期撞 uq_hasn_membership_active_epoch。

PG 不可达跳过；每用例 uuid 派生全新会话，末尾清理自身行。
"""

from __future__ import annotations

import uuid

import pytest
import pytest_asyncio
import sqlalchemy as sa

from sqlalchemy.exc import IntegrityError, OperationalError
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from backend.app.hasn_im.application import membership_service as svc
from backend.app.hasn_im.application import message_service
from backend.app.hasn_im.domain import membership as membership_domain
from backend.database.db import SQLALCHEMY_DATABASE_URL

pytestmark = pytest.mark.asyncio


@pytest_asyncio.fixture
async def sessionmaker_pg():
    engine = create_async_engine(SQLALCHEMY_DATABASE_URL, poolclass=NullPool, future=True)
    try:
        async with engine.connect() as conn:
            await conn.execute(sa.text('SELECT 1'))
    except OperationalError as exc:
        await engine.dispose()
        pytest.skip(f'PostgreSQL 不可达，跳过 R2-03 成员周期测试：{exc}')
    try:
        yield async_sessionmaker(engine, expire_on_commit=False)
    finally:
        await engine.dispose()


async def _make_group_conversation(session) -> str:
    """建一条最小 group 会话（current_seq=0），返回 UUID 字符串。"""
    conv_id = str(uuid.uuid4())
    await session.execute(
        sa.text(
            'INSERT INTO public.hasn_conversations '
            '(id, type, group_id, participant_a_id, participant_a_type, status, current_seq) '
            "VALUES (:id, 'group', :gid, :creator, 'human', 'active', 0)"
        ),
        {'id': conv_id, 'gid': f'g:{500000 + (int(conv_id[:6], 16) % 100000)}', 'creator': 'h_creator'},
    )
    return conv_id


async def _insert_message(session, conv_id: str, seq: int, from_id: str, *, status: int = 1) -> None:
    """插入一条已分配 conversation_seq 的消息（模拟已落库的权威消息）。"""
    await session.execute(
        sa.text(
            'INSERT INTO public.hasn_messages '
            '(conversation_id, conversation_seq, from_id, to_id, content_type, content, status) '
            "VALUES (:cid, :seq, :from_id, 'g:x', 1, '{}'::jsonb, :status)"
        ),
        {'cid': conv_id, 'seq': seq, 'from_id': from_id, 'status': status},
    )
    await session.execute(
        sa.text('UPDATE public.hasn_conversations SET current_seq = :seq WHERE id = :cid'),
        {'cid': conv_id, 'seq': seq},
    )


async def _cleanup(sessionmaker, conv_id: str) -> None:
    async with sessionmaker() as session:
        for tbl in ('hasn_messages', 'hasn_conversation_memberships', 'hasn_unread_projection'):
            await session.execute(
                sa.text(f'DELETE FROM public.{tbl} WHERE conversation_id = :cid'),
                {'cid': conv_id},
            )
        await session.execute(
            sa.text('DELETE FROM public.hasn_conversations WHERE id = :cid'),
            {'cid': conv_id},
        )
        await session.commit()


# ---------- 域纯函数（无 DB） ----------


async def test_domain_visibility_and_read_seq_pure() -> None:
    """§4.2/§4.3 纯不变量：可见区间谓词 + read_seq 单调 clamp + 未读谓词。"""
    # 可见区间：seq >= joined AND (left IS NULL OR seq <= left)
    assert membership_domain.is_message_visible_in_epoch(4, 4, None) is True
    assert membership_domain.is_message_visible_in_epoch(3, 4, None) is False  # 加入前
    assert membership_domain.is_message_visible_in_epoch(6, 4, 5) is False  # 离开后
    assert membership_domain.is_message_visible_in_epoch(5, 4, 5) is True  # 恰在上界

    # read_seq = max(old, min(incoming, upper))
    assert membership_domain.advance_read_seq(3, 5, 5) == 5
    assert membership_domain.advance_read_seq(5, 3, 5) == 5  # 不退
    assert membership_domain.advance_read_seq(5, 100, 5) == 5  # clamp
    assert membership_domain.rejoin_read_seq(8) == 7  # joined-1

    # 未读谓词：自己发的不计
    assert (
        membership_domain.counts_toward_unread(
            conversation_seq=4,
            read_seq=3,
            joined_seq=1,
            left_seq=None,
            is_visible=True,
            sender_owner_id='h_a',
            viewer_owner_id='h_b',
        )
        is True
    )
    assert (
        membership_domain.counts_toward_unread(
            conversation_seq=4,
            read_seq=3,
            joined_seq=1,
            left_seq=None,
            is_visible=True,
            sender_owner_id='h_b',
            viewer_owner_id='h_b',  # 自己发的
        )
        is False
    )


# ---------- 退出/重入 + 两段可见区间 ----------


async def test_leave_rejoin_two_visible_segments(sessionmaker_pg) -> None:
    """退出闭合不删行 + 重入建新周期 + 可见并集恰为两段区间（§4.2 D8 核心验收）。"""
    async with sessionmaker_pg() as session:
        conv_id = await _make_group_conversation(session)
        member = f'h_{uuid.uuid4().hex[:10]}'
        # seq 1..3：成员加入前
        for s in (1, 2, 3):
            await _insert_message(session, conv_id, s, from_id='h_other')
        # 加入（current_seq=3 → joined_seq=4）
        ep1 = await svc.join_epoch(session, conv_id, member, current_seq=3)
        assert ep1.joined_seq == 4 and ep1.read_seq == 3 and ep1.left_seq is None
        assert ep1.history_complete_from_seq == 4
        # seq 4..5：在群期间
        for s in (4, 5):
            await _insert_message(session, conv_id, s, from_id='h_other')
        # 退出（current_seq=5 → left_seq=5·不删行）
        left = await svc.leave_epoch(session, conv_id, member, current_seq=5)
        assert left is not None and left.left_seq == 5 and left.state == 'left'
        # seq 6..7：离开期间（不可见）
        for s in (6, 7):
            await _insert_message(session, conv_id, s, from_id='h_other')
        # 重入（current_seq=7 → 新行·joined_seq=8）
        ep2 = await svc.rejoin_epoch(session, conv_id, member, current_seq=7)
        assert ep2.id != ep1.id and ep2.joined_seq == 8 and ep2.left_seq is None
        assert ep2.history_complete_from_seq == 8
        # seq 8..9：重入后
        for s in (8, 9):
            await _insert_message(session, conv_id, s, from_id='h_other')
        await session.commit()

        # 两段可见区间并集 = [4,5] ∪ [8,9]，加入前(1-3)与离开期间(6-7)均不可见
        visible = await svc.list_visible_message_seqs(session, conv_id, member)
        assert visible == [4, 5, 8, 9], f'两段可见区间应为 [4,5,8,9]，实得 {visible}'

        # 退出+重入 = 两行周期并存（旧闭合 + 新活动）
        rows = (
            await session.execute(
                sa.text(
                    'SELECT count(*) FROM public.hasn_conversation_memberships '
                    'WHERE conversation_id = :cid AND member_hasn_id = :m'
                ),
                {'cid': conv_id, 'm': member},
            )
        ).scalar()
        assert rows == 2, f'退出+重入应留两段周期行，实得 {rows}'
    await _cleanup(sessionmaker_pg, conv_id)


# ---------- read_seq 单调推进（DB 侧） ----------


async def test_advance_read_seq_monotonic_and_clamped(sessionmaker_pg) -> None:
    """活动周期 read_seq 只进不退、clamp 到 current_seq（§4.3）。"""
    async with sessionmaker_pg() as session:
        conv_id = await _make_group_conversation(session)
        member = f'h_{uuid.uuid4().hex[:10]}'
        for s in (1, 2, 3, 4, 5):
            await _insert_message(session, conv_id, s, from_id='h_other')
        await svc.join_epoch(session, conv_id, member, current_seq=0)  # joined=1, read=0
        # 推进到 3
        assert await svc.advance_read_seq(session, conv_id, member, incoming_seq=3, current_seq=5) == 3
        # 陈旧低值不回退
        assert await svc.advance_read_seq(session, conv_id, member, incoming_seq=1, current_seq=5) == 3
        # 越界 clamp 到 current_seq=5
        assert await svc.advance_read_seq(session, conv_id, member, incoming_seq=99, current_seq=5) == 5
        await session.commit()
    await _cleanup(sessionmaker_pg, conv_id)


# ---------- 未读权威计数 + 投影 reconciler ----------


async def test_compute_unread_and_projection(sessionmaker_pg) -> None:
    """未读只从序号重算：>read_seq、可见区间内、未撤回、非本人发。投影 reconciler 落一致值。"""
    async with sessionmaker_pg() as session:
        conv_id = await _make_group_conversation(session)
        member = f'h_{uuid.uuid4().hex[:10]}'
        await svc.join_epoch(session, conv_id, member, current_seq=0)  # joined=1, read=0
        await _insert_message(session, conv_id, 1, from_id='h_other')  # 计未读
        await _insert_message(session, conv_id, 2, from_id=member)  # 自己发·不计
        await _insert_message(session, conv_id, 3, from_id='h_other')  # 计未读
        await _insert_message(session, conv_id, 4, from_id='h_other', status=4)  # 撤回·不计
        await session.commit()

        assert await svc.compute_unread(session, conv_id, member) == 2
        assert await svc.rebuild_unread_projection(session, conv_id, member, current_seq=4) == 2
        await session.commit()

        # 读到已读 seq 3 后未读归零
        await svc.advance_read_seq(session, conv_id, member, incoming_seq=3, current_seq=4)
        await session.commit()
        assert await svc.compute_unread(session, conv_id, member) == 0
    await _cleanup(sessionmaker_pg, conv_id)


async def test_increment_unread_rebuilds_from_messages_when_seq_has_hole(
    sessionmaker_pg,
) -> None:
    """合法 seq 空洞不得被误算成未读消息。"""
    async with sessionmaker_pg() as session:
        conv_id = await _make_group_conversation(session)
        member = f'h_{uuid.uuid4().hex[:10]}'
        await svc.join_epoch(
            session,
            conv_id,
            member,
            current_seq=0,
        )
        # seq=1 可由撤回、历史抑制迁移或失败事务后的保留游标形成空洞。
        await _insert_message(
            session,
            conv_id,
            2,
            from_id='h_other',
        )
        await message_service.increment_unread_for(
            session,
            conv_id,
            member,
        )
        unread = await session.scalar(
            sa.text(
                'SELECT unread_count '
                'FROM public.hasn_unread_projection '
                'WHERE conversation_id = :cid '
                'AND member_hasn_id = :member'
            ),
            {'cid': conv_id, 'member': member},
        )
        assert unread == 1
        await session.commit()
    await _cleanup(sessionmaker_pg, conv_id)


# ---------- 唯一约束兜底 ----------


async def test_two_active_epochs_rejected_by_partial_unique(sessionmaker_pg) -> None:
    """同 (会话, 成员) 两个活动周期（left_seq IS NULL）撞 uq_hasn_membership_active_epoch。"""
    conv_id = None
    member = f'h_{uuid.uuid4().hex[:10]}'
    with pytest.raises(IntegrityError):
        async with sessionmaker_pg() as session:
            conv_id = await _make_group_conversation(session)
            await svc.join_epoch(session, conv_id, member, current_seq=0)
            await svc.join_epoch(session, conv_id, member, current_seq=0)  # 第二个活动周期 → 撞唯一
            await session.commit()
    if conv_id:
        await _cleanup(sessionmaker_pg, conv_id)


async def test_direct_permanent_epochs_both_parties(sessionmaker_pg) -> None:
    """direct 双方永久 epoch：两方各建活动周期（left_seq NULL），统一 ACL/read 模型（§4.2）。"""
    async with sessionmaker_pg() as session:
        conv_id = await _make_group_conversation(session)  # 复用建会话，语义按 direct 用
        a, b = f'h_{uuid.uuid4().hex[:8]}', f'a_{uuid.uuid4().hex[:8]}'
        ea = await svc.join_epoch(session, conv_id, a, current_seq=0, member_type='human', permanent=True)
        eb = await svc.join_epoch(session, conv_id, b, current_seq=0, member_type='agent', permanent=True)
        await session.commit()
        assert ea.left_seq is None and eb.left_seq is None
        assert await svc.get_active_epoch(session, conv_id, a) is not None
        assert await svc.get_active_epoch(session, conv_id, b) is not None
    await _cleanup(sessionmaker_pg, conv_id)
