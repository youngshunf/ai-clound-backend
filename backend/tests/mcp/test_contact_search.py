"""平台工具 · hasn.contact.search / hasn.contact.list 真实 service 测试（禁 mock）。

打通"搜联系人 → 拿 contact_hasn_id → hasn.message.send"闭环：
- search 按昵称/唤星号/备注名子串命中好友；
- include_agents 带出 human 好友名下的 active agent（用于"给好友的 agent 发消息"）。

需活体 DB（本地 15432）：
    DATABASE_PORT=15432 pytest backend/tests/mcp/test_contact_search.py
无 DB 时跳过（不伪造）。
"""

from __future__ import annotations

import uuid

import pytest

from backend.app.mcp.auth import AgentContext
from backend.app.mcp.tools.contact import ContactListTool, ContactSearchTool


def _agent_ctx(owner_hasn_id: str) -> AgentContext:
    return AgentContext(
        hasn_id='a_contact_search_test',
        owner_id=1,
        agent_status='active',
        metadata={},
        owner_hasn_id=owner_hasn_id,
        session_uuid='amk_contact_search_test',
    )


async def _db_reachable() -> bool:
    try:
        from sqlalchemy import text

        from backend.database.db import async_db_session

        async with async_db_session() as db:
            await db.execute(text('SELECT 1'))
    except Exception:
        return False
    else:
        return True


def test_contact_tools_reuse_contact_read_scope() -> None:
    """list / search 都复用 contact:read（不新增 scope）。"""
    assert ContactListTool().required_scopes == ['contact:read']
    assert ContactSearchTool().required_scopes == ['contact:read']


def test_contact_search_requires_query() -> None:
    """search 的 input_schema 必填 query。"""
    assert ContactSearchTool().input_schema.get('required') == ['query']


@pytest.mark.asyncio
async def test_contact_search_empty_query_short_circuits() -> None:
    """空 query 直接返回空，不触 DB、不报错。"""
    result = await ContactSearchTool().execute(_agent_ctx('h_no_such_owner'), {'query': '   '})
    assert result == {'contacts': [], 'total': 0, 'query': ''}


@pytest.mark.asyncio
async def test_contact_search_by_nickname_surfaces_friend_and_agents() -> None:
    """真实 DB：按昵称搜到 human 好友，include_agents 默认带出其名下 active agent（零 mock）。"""
    if not await _db_reachable():
        pytest.skip('需活体 DB（DATABASE_PORT=15432）；无 DB 时跳过，不伪造')

    from sqlalchemy import delete

    from backend.app.hasn.model.hasn_agents import HasnAgents
    from backend.app.hasn.model.hasn_contacts import HasnContacts
    from backend.app.hasn.model.hasn_humans import HasnHumans
    from backend.database.db import async_db_session

    sfx = uuid.uuid4().hex[:8]
    owner_id = f'h_owner_{sfx}'
    friend_id = f'h_friend_{sfx}'
    friend_nick = f'大宝测试{sfx}'
    friend_agent_id = f'a_friendagent_{sfx}'

    async with async_db_session() as db:
        db.add(HasnHumans(hasn_id=friend_id, star_id=f'90{sfx[:4]}', nickname=friend_nick, status='active'))
        db.add(HasnAgents(
            hasn_id=friend_agent_id,
            owner_id=friend_id,
            display_name='大宝的助理',
            star_id=f'90{sfx[:4]}#assistant',
            profession='生活助理',
            status='active',
        ))
        db.add(HasnContacts(
            owner_id=owner_id,
            peer_id=friend_id,
            peer_type='human',
            relation_type='social',
            trust_level=2,
            status='connected',
        ))
        await db.commit()

    try:
        # 1) 按昵称搜索 → 命中 human 好友
        res = await ContactSearchTool().execute(_agent_ctx(owner_id), {'query': friend_nick})
        ids = {c['contact_hasn_id'] for c in res['contacts']}
        assert friend_id in ids, '按昵称应搜到 human 好友'

        # 2) include_agents 默认 true → 带出好友名下 agent（可直接作 message.send 的 to）
        agent_rows = [c for c in res['contacts'] if c['contact_hasn_id'] == friend_agent_id]
        assert agent_rows, 'include_agents 默认应带出好友名下的 active agent'
        assert agent_rows[0]['peer_type'] == 'agent'
        assert agent_rows[0]['owner_hasn_id'] == friend_id

        # 3) 不匹配 → 空，不静默返回全量
        res_none = await ContactSearchTool().execute(_agent_ctx(owner_id), {'query': 'zzz_no_match_zzz'})
        assert res_none['total'] == 0

        # 4) list + query/include_agents 等价命中
        res_list = await ContactListTool().execute(
            _agent_ctx(owner_id), {'query': friend_nick, 'include_agents': True}
        )
        list_ids = {c['contact_hasn_id'] for c in res_list['contacts']}
        assert friend_id in list_ids and friend_agent_id in list_ids

        # 5) search 关掉 include_agents → 只回好友本人，不带 agent
        res_noagent = await ContactSearchTool().execute(
            _agent_ctx(owner_id), {'query': friend_nick, 'include_agents': False}
        )
        noagent_ids = {c['contact_hasn_id'] for c in res_noagent['contacts']}
        assert friend_id in noagent_ids and friend_agent_id not in noagent_ids
    finally:
        async with async_db_session() as db:
            await db.execute(delete(HasnContacts).where(HasnContacts.owner_id == owner_id))
            await db.execute(delete(HasnAgents).where(HasnAgents.hasn_id == friend_agent_id))
            await db.execute(delete(HasnHumans).where(HasnHumans.hasn_id == friend_id))
            await db.commit()
