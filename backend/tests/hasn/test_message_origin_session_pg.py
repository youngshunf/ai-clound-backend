"""doc14 S1 落库验收：origin_session_id + mission_note 真实落 PG（零 mock）。

契约：
- ``persist_message(origin_session_id=...)`` → ``hasn_messages.origin_session_id`` 列（带/不带两态）；
- ``get_or_create_conversation(mission_note=...)`` → ``hasn_conversations.mission_note`` +
  ``mission_note_owner_id`` **仅新建时写**，既有会话再传不覆盖（发起层语义，doc14 §6.5）。

事务内验收范式（对齐 test_message_owner_id_backfill）：未提交事务里写+读回，退出即回滚，
不落库不污染。需 DATABASE_PORT=15432（本地 huanxing 库）；无 DB 时跳过，不伪造。
"""

from __future__ import annotations

import uuid

import pytest

from backend.app.hasn.service.message_router import get_or_create_conversation, persist_message
from backend.database.db import async_db_session

pytestmark = pytest.mark.asyncio(loop_scope='module')

_SESSION = 'sess_work_doc14'


async def _db_reachable() -> bool:
    try:
        from sqlalchemy import text

        async with async_db_session() as db:
            await db.execute(text('SELECT 1'))
    except Exception:
        return False
    else:
        return True


async def test_persist_message_lands_origin_session_id() -> None:
    """带会话上下文发出的消息，溯源落列；无上下文（人手动发/直调）留 NULL。"""
    if not await _db_reachable():
        pytest.skip('需活体 DB（DATABASE_PORT=15432）；无 DB 时跳过，不伪造')

    async with async_db_session() as db:
        suffix = uuid.uuid4().hex[:12]
        sender = f'a_doc14_sender_{suffix}'
        recipient = f'h_doc14_recip_{suffix}'
        conv = await get_or_create_conversation(db, sender, 'agent', recipient, 'human', 'social')

        traced = await persist_message(
            db=db,
            conversation_id=str(conv.id),
            from_id=sender,
            to_id=recipient,
            content={'text': f'带溯源_{suffix}'},
            origin_session_id=_SESSION,
        )
        assert traced.origin_session_id == _SESSION

        # 无会话上下文 → NULL（never over-block：照常发，只是不参与回灌）
        untraced = await persist_message(
            db=db,
            conversation_id=str(conv.id),
            from_id=sender,
            to_id=recipient,
            content={'text': f'无溯源_{suffix}'},
        )
        assert untraced.origin_session_id is None

        # 读回真实列值（而非仅信 ORM 实例内存态）
        from sqlalchemy import text

        row = await db.execute(
            text('SELECT origin_session_id FROM hasn_messages WHERE id = :mid'), {'mid': traced.id}
        )
        assert row.scalar_one() == _SESSION

        await db.rollback()


async def test_mission_note_written_on_create_only() -> None:
    """差事背景只在新建会话时写：既有会话再传 mission_note 一律不覆盖（发起层语义）。"""
    if not await _db_reachable():
        pytest.skip('需活体 DB（DATABASE_PORT=15432）；无 DB 时跳过，不伪造')

    async with async_db_session() as db:
        suffix = uuid.uuid4().hex[:12]
        sender = f'a_doc14_mn_{suffix}'
        peer = f'h_doc14_mnpeer_{suffix}'
        note = f'替主人约王工聊周五联调_{suffix}'

        created = await get_or_create_conversation(
            db, sender, 'agent', peer, 'human', 'social',
            mission_note=note,
            mission_note_owner_id='h_doc14_master',
        )
        assert created.mission_note == note
        assert created.mission_note_owner_id == 'h_doc14_master'

        # 同一对参与者再发（会话已存在）→ 早退复用，不覆盖原差事背景
        again = await get_or_create_conversation(
            db, sender, 'agent', peer, 'human', 'social',
            mission_note='试图覆盖的新背景',
            mission_note_owner_id='h_doc14_imposter',
        )
        assert str(again.id) == str(created.id), '应复用既有会话'
        assert again.mission_note == note, '既有会话的差事背景不可被后续发送覆盖'
        assert again.mission_note_owner_id == 'h_doc14_master'

        await db.rollback()


async def test_conversation_without_mission_note_stays_null() -> None:
    """不传 mission_note → 两列都留 NULL（归属 owner 不凭空写）。"""
    if not await _db_reachable():
        pytest.skip('需活体 DB（DATABASE_PORT=15432）；无 DB 时跳过，不伪造')

    async with async_db_session() as db:
        suffix = uuid.uuid4().hex[:12]
        conv = await get_or_create_conversation(
            db, f'a_doc14_plain_{suffix}', 'agent', f'h_doc14_plain_{suffix}', 'human', 'social'
        )
        assert conv.mission_note is None
        assert conv.mission_note_owner_id is None
        await db.rollback()
