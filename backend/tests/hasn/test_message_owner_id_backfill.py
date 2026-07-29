"""doc18 P0 回归：persist_message 回填 owner_id → owner 透明视图可读（真实 PG，零 mock）。

契约（doc18 §5.0.3）：1:1 消息落库 owner_id = 收件方 owner，且能被该 owner 的透明视图 /
hasn.message.search（硬过滤 `WHERE owner_id`）读到——此前 route 路径从不填 owner_id，
75% 消息行 owner_id 为空、收件方分身检索不到「对方告诉过我的事」，L3 聊天记录兜底失效。
群消息不填 owner_id（走 conversation_id 归属），本测试一并守住「传空即空」。

事务内验收范式（对齐 test_agent_message_read_service）：在**未提交**事务里跑
get_or_create_conversation + persist_message + 读回，退出即回滚，不落库不污染。
需 DATABASE_PORT=15432（本地 huanxing 库）；无 DB 时跳过，不伪造。
"""

from __future__ import annotations

import uuid

import pytest

from backend.app.hasn.service.agent_message_read_service import agent_message_read_service as svc
from backend.app.hasn_im.application.message_service import get_or_create_conversation, persist_message
from backend.database.db import async_db_session

pytestmark = pytest.mark.asyncio(loop_scope='session')


async def _db_reachable() -> bool:
    try:
        from sqlalchemy import text

        async with async_db_session() as db:
            await db.execute(text('SELECT 1'))
    except Exception:
        return False
    else:
        return True


async def test_persist_message_1to1_backfills_recipient_owner_and_is_readable() -> None:
    """A2H：分身发给人类 → 收件方（人类=owner）透明视图能读到；另一 owner 隔离读不到。"""
    if not await _db_reachable():
        pytest.skip('需活体 DB（DATABASE_PORT=15432）；无 DB 时跳过，不伪造')

    async with async_db_session() as db:
        suffix = uuid.uuid4().hex[:12]
        sender = f'a_p0_sender_{suffix}'  # 发送分身（其 owner 是别人，与本测试无关）
        recipient_owner = f'h_p0_recip_{suffix}'  # 收件人本人即 owner
        other_owner = f'h_p0_other_{suffix}'
        marker = f'我住在杭州_{suffix}'

        conv = await get_or_create_conversation(db, sender, 'agent', recipient_owner, 'human', 'social')
        msg = await persist_message(
            db=db,
            conversation_id=str(conv.id),
            from_id=sender,
            to_id=recipient_owner,
            content={'text': marker},
            owner_id=recipient_owner,
        )
        # 写路径契约：owner_id 落到收件方 owner
        assert msg.owner_id == recipient_owner, 'persist_message 应把 owner_id 落为收件方 owner'

        # 读路径契约：收件方 owner 透明视图（WHERE owner_id）能读到这条 route 落库消息
        page = await svc.list_messages(db, recipient_owner, limit=50)
        assert any(m['content'] == marker for m in page['messages']), '收件方 owner 应能读到该消息'

        # owner 隔离：另一 owner 绝不串读
        other = await svc.list_messages(db, other_owner, limit=50)
        assert not any(m['content'] == marker for m in other['messages']), 'owner 隔离：他人读不到'

        await db.rollback()


async def test_persist_message_group_leaves_owner_id_empty() -> None:
    """群消息不填 owner_id：owner 透明视图（WHERE owner_id）读不到，按 conversation_id 归属。"""
    if not await _db_reachable():
        pytest.skip('需活体 DB（DATABASE_PORT=15432）；无 DB 时跳过，不伪造')

    from sqlalchemy import text

    async with async_db_session() as db:
        suffix = uuid.uuid4().hex[:12]
        sender = f'a_p0_grp_sender_{suffix}'
        someone = f'h_p0_grp_someone_{suffix}'
        group_conv_id = str(uuid.uuid4())
        group_id = f'g:{500000 + (int(suffix, 16) % 100000)}'
        marker = f'群里发言_{suffix}'

        # R2-02：persist_message 现在同事务内 allocate_seq（UPDATE hasn_conversations RETURNING），
        # 要求会话行必须已存在。生产群分支里 route_message 先 get_group_conversation 拿到活跃群会话
        # 才 persist，故这里如实先建一条最小群会话行（同一未提交事务内，末尾 rollback 不落库）。
        await db.execute(
            text(
                'INSERT INTO hasn_conversations '
                '(id, type, group_id, participant_a_id, participant_a_type, status, current_seq) '
                "VALUES (:id, 'group', :gid, :creator, 'human', 'active', 0)"
            ),
            {'id': group_conv_id, 'gid': group_id, 'creator': f'h_p0_grp_creator_{suffix}'},
        )

        # 群消息落库不传 owner_id（模拟 route_message 群分支）；直接给 conversation_id，to_id 为 g:*
        msg = await persist_message(
            db=db,
            conversation_id=group_conv_id,
            from_id=sender,
            to_id=group_id,
            content={'text': marker},
        )
        assert msg.owner_id is None, '群消息 owner_id 应留空（按 conversation_id 归属）'

        # 任取一 owner 的透明视图都读不到（owner_id 为空 → 不落任何 owner 的 WHERE owner_id）
        page = await svc.list_messages(db, someone, limit=50)
        assert not any(m['content'] == marker for m in page['messages'])

        await db.rollback()
