"""知识库 register-on-write 守卫（真实 PG + 真实 RAGFlow，零 mock）。

守的是产物自动登记铁律（doc31）在知识库的落地：**分身建库/写文档，主人必须在工作会话资源栏
看得见**。三条不变量：
1. 建库 → `hasn_artifacts` 出一条 `hasn://knowledge/kbs/{kb_id}` 的 dataset 产物；
2. 写文档 → 出一条 `hasn://knowledge/documents/{doc_id}` 的 document 产物（库与文档各自独立可见）；
3. 工作会话 id 经 ContextVar 通道（`_hasn_session_id` 由分发入口剥离后落入）绑上产物——
   这是 AI-Native 应用工具面唯一能拿到会话 id 的路（handler 只收 AgentTokenPayload）。

历史：知识库漏登记不是「忘了加」，而是 `ai_native_runtime_gateway` 那条工具面**根本没有**
session_id 通道（deck 走的 MCP 直连面才有）。本测试同时钉死通道与登记。
"""

from __future__ import annotations

import uuid

from datetime import datetime, timedelta

import pytest
import sqlalchemy as sa

from backend.app.hasn.model.hasn_artifacts import HasnArtifacts
from backend.app.hasn_knowledge.service import tool_handlers
from backend.app.mcp.context import clear_current_work_session_id, set_current_work_session_id
from backend.common.dataclasses import AgentTokenPayload

pytestmark = pytest.mark.asyncio


def _agent(owner: str, agent_hasn_id: str) -> AgentTokenPayload:
    return AgentTokenPayload(
        agent_hasn_id=agent_hasn_id,
        agent_name='知识管理专家',
        owner_hasn_id=owner,
        owner_user_id=0,
        session_uuid=uuid.uuid4().hex,
        expire_time=datetime.now() + timedelta(hours=1),
    )


async def _artifact_of(session, agent_hasn_id: str, resource_uri: str) -> HasnArtifacts | None:
    return (
        (
            await session.execute(
                sa.select(HasnArtifacts).where(
                    HasnArtifacts.agent_hasn_id == agent_hasn_id,
                    HasnArtifacts.resource_uri == resource_uri,
                    HasnArtifacts.status == 'active',
                )
            )
        )
        .scalars()
        .first()
    )


async def test_create_kb_and_write_doc_register_artifacts_with_session(session, ragflow_ready) -> None:
    tag = uuid.uuid4().hex[:8]
    owner = f'h_reg_{tag}'
    agent = _agent(owner, f'a_reg_{tag}')
    work_session_id = f'ws_{tag}'

    # 模拟分发入口：把系统注入的 `_hasn_session_id` 落进 ContextVar（两个真实入口都这么做）。
    set_current_work_session_id(work_session_id)
    try:
        kb = await tool_handlers.handle_knowledge_create_kb(
            session,
            agent,
            {'name': f'登记测试库-{tag}', 'cover_asset_uri': f'hasn://asset/cover_{tag}'},
        )
        kb_artifact = await _artifact_of(session, agent.agent_hasn_id, f'hasn://knowledge/kbs/{kb["id"]}')
        assert kb_artifact is not None, '分身建库必须登记产物，否则工作会话资源栏看不见'
        assert kb_artifact.session_id == work_session_id, '产物必须绑上工作会话，否则挂不进会话资源栏'
        assert kb_artifact.kind == 'dataset'
        assert kb_artifact.owner_hasn_id == owner

        doc = await tool_handlers.handle_knowledge_write_doc(
            session,
            agent,
            {'kb_id': str(kb['id']), 'title': f'第一篇-{tag}', 'content': '# 标题\n\n正文内容。'},
        )
        doc_artifact = await _artifact_of(session, agent.agent_hasn_id, f'hasn://knowledge/documents/{doc["doc_id"]}')
        assert doc_artifact is not None, '分身写的每篇文档都要独立登记（只登记库=主人不知道产出了什么）'
        assert doc_artifact.session_id == work_session_id
        assert doc_artifact.kind == 'document'

        # 幂等：同一文档再写一次不重复登记（键 = agent + dispatch_id + resource_uri）。
        await tool_handlers.handle_knowledge_write_doc(
            session,
            agent,
            {'doc_id': str(doc['doc_id']), 'title': f'第一篇-{tag}（改）', 'content': '# 标题\n\n改过的正文。'},
        )
        rows = (
            (
                await session.execute(
                    sa.select(HasnArtifacts).where(
                        HasnArtifacts.agent_hasn_id == agent.agent_hasn_id,
                        HasnArtifacts.resource_uri == f'hasn://knowledge/documents/{doc["doc_id"]}',
                        HasnArtifacts.status == 'active',
                    )
                )
            )
            .scalars()
            .all()
        )
        assert len(rows) == 1, '反复写同一文档只能有一条 active 产物行'
    finally:
        clear_current_work_session_id()


async def test_register_without_session_still_lands_in_artifact_tab(session, ragflow_ready) -> None:
    """主会话直调（无 `_hasn_session_id`）：session_id 为空，但产物仍要登记进分身产物 tab。"""
    tag = uuid.uuid4().hex[:8]
    owner = f'h_nos_{tag}'
    agent = _agent(owner, f'a_nos_{tag}')

    clear_current_work_session_id()
    kb = await tool_handlers.handle_knowledge_create_kb(
        session,
        agent,
        {'name': f'主会话库-{tag}', 'cover_asset_uri': f'hasn://asset/cover_{tag}'},
    )
    artifact = await _artifact_of(session, agent.agent_hasn_id, f'hasn://knowledge/kbs/{kb["id"]}')
    assert artifact is not None
    assert artifact.session_id is None
