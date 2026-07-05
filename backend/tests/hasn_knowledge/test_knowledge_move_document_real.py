"""hasn.knowledge.move_document 工具集成测试（真实 PG + 真实 RAGFlow，零 mock）。

覆盖：可达分身把文档移进目录 / 移回库根（folder_id 变更、落库核对）；缺参如实报错；
维度②越权（restricted 白名单不含该库）如实拒（ForbiddenError）。
移动是纯归属变更，不触发引擎重索引——但建库/建文档仍需真实 RAGFlow，故沿用 ragflow_ready 夹具。
"""

from __future__ import annotations

import uuid

from datetime import datetime, timedelta

import pytest

from backend.app.hasn_knowledge.service import tool_handlers
from backend.app.hasn_knowledge.service.knowledge_service import knowledge_service
from backend.common.dataclasses import AgentTokenPayload
from backend.common.exception import errors

pytestmark = pytest.mark.asyncio


def _agent(owner: str, agent_hasn_id: str) -> AgentTokenPayload:
    """构造一个测试用 Agent 身份载荷（身份恒取自此，绝不读 payload 业务字段）。"""
    return AgentTokenPayload(
        agent_hasn_id=agent_hasn_id,
        agent_name='整理专家',
        owner_hasn_id=owner,
        owner_user_id=0,
        session_uuid=uuid.uuid4().hex,
        expire_time=datetime.now() + timedelta(hours=1),
    )


async def test_move_document_between_folder_and_root(session, ragflow_ready) -> None:
    tag = uuid.uuid4().hex[:8]
    owner = f'h_mv_{tag}'
    agent = _agent(owner, f'a_mv_{tag}')

    kb = await knowledge_service.create_kb(session, owner, name=f'整理库-{tag}', description='move 测试')
    try:
        folder = await knowledge_service.create_folder(session, owner, kb['id'], name='归档', parent_id=None)
        # 建一篇库根原生文档（初始 folder_id 为 None）
        doc = await knowledge_service.create_native_document(
            session,
            owner,
            kb['id'],
            title=f'待归类-{tag}',
            content=f'# 待归类\n\n唤星 move 测试标记 {tag}',
            source='ui',
        )
        assert doc['folder_id'] is None

        # 分身默认 inherit → 可达；移进目录
        moved = await tool_handlers.handle_knowledge_move_document(
            session, agent, {'doc_id': doc['id'], 'folder_id': folder['id']}
        )
        assert moved['moved'] is True
        assert moved['folder_id'] == folder['id']
        # 落库核对
        after = await knowledge_service.get_document(session, owner, doc['id'])
        assert after['folder_id'] == folder['id']

        # 移回库根
        back = await tool_handlers.handle_knowledge_move_document(
            session, agent, {'doc_id': doc['id'], 'move_to_root': True}
        )
        assert back['moved'] is True
        assert back['folder_id'] is None

        # folder_id 与 move_to_root 都没给 → 如实报错，不静默
        with pytest.raises(errors.RequestError):
            await tool_handlers.handle_knowledge_move_document(session, agent, {'doc_id': doc['id']})

        # 维度②越权：restricted 白名单不含本库的分身移动本库文档 → 如实拒
        blind = _agent(owner, f'a_blind_{tag}')
        await knowledge_service.put_agent_grant(session, owner, blind.agent_hasn_id, mode='restricted', kb_ids=[])
        with pytest.raises(errors.ForbiddenError):
            await tool_handlers.handle_knowledge_move_document(
                session, blind, {'doc_id': doc['id'], 'folder_id': folder['id']}
            )
    finally:
        # 清理：删库级联删引擎 dataset + 文档行
        await knowledge_service.delete_kb(session, owner, kb['id'])
    assert await knowledge_service.list_kbs(session, owner) == []
