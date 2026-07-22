"""HASN IM 已读端点授权回归（真实 PostgreSQL，无 mock）。"""

from __future__ import annotations

import uuid

import pytest

from fastapi import HTTPException
from sqlalchemy import delete, select

from backend.app.hasn.api.v1.app.hasn_im import MarkReadReq, mark_conversation_read
from backend.app.hasn.model import HasnConversations, HasnUnreadCounts
from backend.database.db import async_db_session

pytestmark = pytest.mark.asyncio


async def test_mark_read_rejects_non_participant_for_valid_conversation_id() -> None:
    """合法 UUID 也必须先验证参与者，不能给他人会话写入已读游标。"""
    suffix = uuid.uuid4().hex[:12]
    owner_id = f'h_im_owner_{suffix}'
    peer_id = f'h_im_peer_{suffix}'
    outsider_id = f'h_im_outsider_{suffix}'

    async with async_db_session() as db:
        conversation = HasnConversations(
            type='direct',
            participant_a_id=owner_id,
            participant_b_id=peer_id,
            participant_a_type='human',
            participant_b_type='human',
            status='active',
        )
        db.add(conversation)
        await db.flush()
        conversation_id = str(conversation.id)

        try:
            with pytest.raises(HTTPException) as exc_info:
                await mark_conversation_read(
                    db,
                    MarkReadReq(last_msg_id=99),
                    conversation_id,
                    {'hasn_id': outsider_id},
                )
            assert exc_info.value.status_code == 403
        finally:
            # 端点成功路径会自行提交；无论断言结果如何都清理，避免真实库留下测试数据。
            await db.execute(delete(HasnUnreadCounts).where(HasnUnreadCounts.conversation_id == conversation.id))
            await db.execute(delete(HasnConversations).where(HasnConversations.id == conversation.id))
            await db.commit()
