"""平台工具 · hasn.contact.request 真实 service 测试（禁 mock）。

打通"搜陌生人(hasn.user.search) → 拿 HASN ID → 代主人发起加好友"闭环：
- human 目标：审批人=对方本人，落一条 hasn_contact_requests pending；
- agent 目标：审批人=分身主人，信任等级随『请求方↔主人』；已有 pending 幂等返回。

工具与人端 owner 端点共用 `HasnContactsService.request_contact` 单一实现，
故本测试直打 service + 工具 execute，覆盖两分支与典型校验失败。

需活体 DB（本地 15432）：
    DATABASE_PORT=15432 pytest backend/tests/mcp/test_contact_request.py
无 DB 时跳过（不伪造）。
"""

from __future__ import annotations

import uuid

import pytest

from backend.app.mcp.auth import AgentContext
from backend.app.mcp.tools.contact import ContactRequestTool


def _agent_ctx(owner_hasn_id: str) -> AgentContext:
    return AgentContext(
        hasn_id='a_contact_request_test',
        owner_id=1,
        scopes=[],
        agent_status='active',
        metadata={},
        owner_hasn_id=owner_hasn_id,
        session_uuid='amk_contact_request_test',
    )


async def _probe_db_once() -> bool:
    from sqlalchemy import text

    from backend.database.db import async_db_session

    try:
        async with async_db_session() as db:
            await db.execute(text('SELECT 1'))
    except Exception:
        return False
    return True


async def _db_reachable() -> bool:
    # 多个 async 测试串跑时 asyncpg 连接池偶发 "Connection._cancel" 抖动 → 探针重试几次再判死，避免假跳过。
    import asyncio

    for attempt in range(3):
        if await _probe_db_once():
            return True
        if attempt < 2:
            await asyncio.sleep(0.05)
    return False


def test_contact_request_declares_request_scope() -> None:
    """request 工具落 contact:request（与 list/search 的 contact:read 区分）。"""
    assert ContactRequestTool().required_scopes == ['contact:request']


def test_contact_request_requires_target() -> None:
    """input_schema 必填 target。"""
    assert ContactRequestTool().input_schema.get('required') == ['target']


@pytest.mark.asyncio
async def test_contact_request_empty_target_short_circuits() -> None:
    """空 target 直接返回错误，不触 DB、不抛异常。"""
    result = await ContactRequestTool().execute(_agent_ctx('h_no_such_owner'), {'target': '   '})
    assert result == {'ok': False, 'error': 'target 不能为空（对方唤星号或 HASN ID）'}


@pytest.mark.asyncio
async def test_contact_request_unknown_target_returns_error() -> None:
    """目标不存在 → ContactRequestError 被工具转成 ok=False（不抛异常给运行时）。"""
    if not await _db_reachable():
        pytest.skip('需活体 DB（DATABASE_PORT=15432）；无 DB 时跳过，不伪造')
    res = await ContactRequestTool().execute(
        _agent_ctx('h_owner_nonexistent'), {'target': 'h_definitely_not_real_xyz'}
    )
    assert res['ok'] is False
    assert '不存在' in res['error']


@pytest.mark.asyncio
async def test_contact_request_human_target_creates_pending() -> None:
    """真实 DB：代主人向陌生 human 发起好友请求，落一条 pending（零 mock）。"""
    if not await _db_reachable():
        pytest.skip('需活体 DB（DATABASE_PORT=15432）；无 DB 时跳过，不伪造')

    from sqlalchemy import delete

    from backend.app.hasn.model.hasn_contact_requests import HasnContactRequests
    from backend.app.hasn.model.hasn_humans import HasnHumans
    from backend.database.db import async_db_session

    sfx = uuid.uuid4().hex[:8]
    owner_id = f'h_owner_{sfx}'
    target_id = f'h_target_{sfx}'
    target_star = f'70{sfx[:4]}'
    # hasn_humans.user_id 默认 0 且有唯一索引 → 每个 human 需各自唯一且不撞真实用户的高位值
    uid_base = 900_000_000 + (int(sfx, 16) % 50_000_000)

    async with async_db_session() as db:
        db.add(HasnHumans(
            hasn_id=owner_id, star_id=f'71{sfx[:4]}', user_id=uid_base, nickname=f'主人{sfx}', status='active'))
        db.add(HasnHumans(
            hasn_id=target_id, star_id=target_star, user_id=uid_base + 1, nickname=f'陌生人{sfx}', status='active'))
        await db.commit()

    try:
        # 1) 用唤星号发起 → human 分支 pending
        res = await ContactRequestTool().execute(_agent_ctx(owner_id), {'target': target_star, 'message': '交个朋友'})
        assert res['ok'] is True, res
        assert res['status'] == 'pending'
        assert res['to_type'] == 'human'
        assert res['target']['hasn_id'] == target_id
        request_id = res['request_id']

        # 2) 落库核实：pending 行存在、审批人=对方本人、附言落库
        async with async_db_session() as db:
            row = await db.get(HasnContactRequests, request_id)
            assert row is not None
            assert row.from_id == owner_id
            assert row.to_id == target_id
            assert row.to_owner_id == target_id  # human 目标审批人=本人
            assert row.to_type == 'human'
            assert row.status == 'pending'
            assert row.message == '交个朋友'

        # 3) 同一对再发 → 应被 active pending 去重拦截（human 分支抛 ContactRequestError）
        res_dup = await ContactRequestTool().execute(_agent_ctx(owner_id), {'target': target_star})
        assert res_dup['ok'] is False
        assert '待处理' in res_dup['error']

        # 4) 加自己 → '不能添加自己为好友'
        res_self = await ContactRequestTool().execute(_agent_ctx(owner_id), {'target': f'71{sfx[:4]}'})
        assert res_self['ok'] is False
        assert '自己' in res_self['error']
    finally:
        async with async_db_session() as db:
            await db.execute(delete(HasnContactRequests).where(HasnContactRequests.from_id == owner_id))
            await db.execute(delete(HasnHumans).where(HasnHumans.hasn_id.in_([owner_id, target_id])))
            await db.commit()


@pytest.mark.asyncio
async def test_contact_request_agent_target_inherits_trust_and_is_idempotent() -> None:
    """真实 DB：代主人加『好友的分身』→ 审批人=分身主人、trust 随主人关系、重发幂等。"""
    if not await _db_reachable():
        pytest.skip('需活体 DB（DATABASE_PORT=15432）；无 DB 时跳过，不伪造')

    from sqlalchemy import delete

    from backend.app.hasn.model.hasn_agents import HasnAgents
    from backend.app.hasn.model.hasn_contact_requests import HasnContactRequests
    from backend.app.hasn.model.hasn_contacts import HasnContacts
    from backend.app.hasn.model.hasn_humans import HasnHumans
    from backend.database.db import async_db_session

    sfx = uuid.uuid4().hex[:8]
    owner_id = f'h_owner_{sfx}'
    friend_id = f'h_friend_{sfx}'
    agent_id = f'a_targetagent_{sfx}'
    agent_star = f'80{sfx[:4]}#expert'
    # hasn_humans.user_id 默认 0 且有唯一索引 → 每个 human 需各自唯一且不撞真实用户的高位值
    uid_base = 900_000_000 + (int(sfx, 16) % 50_000_000)

    async with async_db_session() as db:
        db.add(HasnHumans(
            hasn_id=owner_id, star_id=f'81{sfx[:4]}', user_id=uid_base, nickname=f'主人{sfx}', status='active'))
        db.add(HasnHumans(
            hasn_id=friend_id, star_id=f'80{sfx[:4]}', user_id=uid_base + 1, nickname=f'好友{sfx}', status='active'))
        db.add(HasnAgents(
            hasn_id=agent_id, owner_id=friend_id, display_name='好友的专家',
            star_id=agent_star, profession='法律顾问', status='active',
        ))
        # 主人与好友本人已是好友、trust=3 → 加好友的分身时 trust 继承=3
        db.add(HasnContacts(
            owner_id=owner_id, peer_id=friend_id, peer_type='human',
            relation_type='social', trust_level=3, status='connected',
        ))
        await db.commit()

    try:
        # 1) 用 HASN ID 加好友的分身 → agent 分支
        res = await ContactRequestTool().execute(_agent_ctx(owner_id), {'target': agent_id})
        assert res['ok'] is True, res
        assert res['to_type'] == 'agent'
        assert res['target']['hasn_id'] == agent_id
        request_id = res['request_id']

        # 2) 落库核实：to_id=分身本体、审批人=分身主人、trust 随主人关系=3
        async with async_db_session() as db:
            row = await db.get(HasnContactRequests, request_id)
            assert row is not None
            assert row.to_id == agent_id
            assert row.to_type == 'agent'
            assert row.to_owner_id == friend_id  # 审批人=分身主人
            assert row.requested_trust_level == 3  # 继承『主人↔好友』trust
            assert row.status == 'pending'

        # 3) 重发同一分身 → 幂等返回同一 pending（不报错、不新建）
        res_again = await ContactRequestTool().execute(_agent_ctx(owner_id), {'target': agent_star})
        assert res_again['ok'] is True
        assert res_again['request_id'] == request_id

        async with async_db_session() as db:
            from sqlalchemy import func, select
            cnt = await db.scalar(
                select(func.count()).select_from(HasnContactRequests)
                .where(HasnContactRequests.from_id == owner_id)
                .where(HasnContactRequests.to_id == agent_id)
            )
            assert cnt == 1, '幂等：重发不应新建第二条 pending'
    finally:
        async with async_db_session() as db:
            await db.execute(delete(HasnContactRequests).where(HasnContactRequests.from_id == owner_id))
            await db.execute(delete(HasnContacts).where(HasnContacts.owner_id == owner_id))
            await db.execute(delete(HasnAgents).where(HasnAgents.hasn_id == agent_id))
            await db.execute(delete(HasnHumans).where(HasnHumans.hasn_id.in_([owner_id, friend_id])))
            await db.commit()
