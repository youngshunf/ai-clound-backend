"""云端挂起(cloud-pend) ask 审批闸门真实联调（AskPend-C）。

锁住福仔 2026-06-08 拍板的修复：分身经 cloud 直连面调云端 MCP 工具命中 `ask` 时，
**云端自己挂起**——发审批卡片给主人 + 阻塞轮询裁决，对 agent 透明（agent 只等工具返回，
不知道 ask 过程）。批准→放行真执行；拒绝/超时→回**工具错误**（绝不把 approval_required 透传）。

回归此前事故：旧 `open_request()` 直接把 `approval_required` 信封当工具结果返回给 agent
（截图里 agent 收到 `areq_...` 请求 ID + “需要你批准后才能执行”），而经 cloud 直连面不经
daemon 中转、daemon 令牌重试拦不到 → ask 链路断、agent 反而知道了 ask 过程。

连真实本地 PostgreSQL，savepoint 事务回滚隔离，零 Mock 零 Fake。
- 被测代码内部用全局 `async_db_session`（`.begin()` 与工厂两种用法）自开会话；测试把它临时
  指到本用例的 savepoint 会话（不真正关闭/提交），让全链路在同一事务里跑、随用例回滚。
"""
from __future__ import annotations

import pytest
from sqlalchemy import text

from backend.app.mcp import ask_gate as ask_gate_module
from backend.app.mcp.ask_gate import ask_approval_gate
from backend.common.exception import errors
from backend.utils.timezone import timezone
from tests.hasn_community.conftest import seed_agent, seed_human

_TOOL = 'community.create_post'
_SCOPES = ['community:post']


def _bind_session(monkeypatch, db) -> None:
    """把全局 async_db_session（`()` 工厂 + `.begin()`）临时指到本用例 savepoint 会话。

    被测方法内部既用 `async with async_db_session.begin() as db:` 落库/发卡，也用
    `async with async_db_session() as db:` 轮询读状态——两种都必须回到同一事务，否则
    种子不可见 / 真提交污染库。
    """

    class _SessionCtx:
        async def __aenter__(self):
            return db

        async def __aexit__(self, *exc):
            return False  # 绝不关闭测试会话（由 fixture 统一回滚）

    class _FakeMaker:
        def __call__(self):
            return _SessionCtx()

        def begin(self):
            return _SessionCtx()

    monkeypatch.setattr('backend.database.db.async_db_session', _FakeMaker())


def _bind_session_no_begin(monkeypatch, db) -> None:
    """同 `_bind_session`，但 `.begin()` 直接抛——锁住「发卡片必须用裸工厂会话」。

    route_message 自带 commit；若发卡片误用 `async_db_session.begin()` 包裹，退出时二次 commit
    会抛异常 → 误判发卡失败 → 工具被即时拒绝（线上「设置每次询问却 0.x 秒被拒」事故根因）。
    这里把 `.begin()` 设为抛错，确保发卡片走 `()` 工厂面，否则用例红。
    """

    class _SessionCtx:
        async def __aenter__(self):
            return db

        async def __aexit__(self, *exc):
            return False

    class _FactoryOnlyMaker:
        def __call__(self):
            return _SessionCtx()

        def begin(self):
            raise AssertionError('发审批卡片不得用 async_db_session.begin()（route_message 自带 commit，会二次提交）')

    monkeypatch.setattr('backend.database.db.async_db_session', _FactoryOnlyMaker())


async def _approval_row(db, request_id: str):
    from backend.app.hasn.crud.crud_hasn_agent_approval_requests import hasn_agent_approval_requests_dao as dao

    return await dao.get_by_request_id(db, request_id)


async def _set_status(db, request_id: str, status: str) -> None:
    """模拟主人裁决：daemon 点卡片 → 云端 grant(approved)/deny(denied) 改状态。"""
    from backend.app.hasn.crud.crud_hasn_agent_approval_requests import hasn_agent_approval_requests_dao as dao

    row = await dao.get_by_request_id(db, request_id)
    await dao.update_model(db, row.id, {'status': status, 'decided_time': timezone.now()})


@pytest.mark.asyncio
async def test_request_and_wait_denies_when_no_owner():
    """无主人 = 没有审批出口 → 直接 denied（零 fake：绝不默认放行），不落任何库。"""
    verdict = await ask_approval_gate.request_and_wait(
        agent_hasn_id='a_no_owner',
        owner_hasn_id=None,
        tool_name=_TOOL,
        required_scopes=_SCOPES,
        default_mode='allow',
        capability_modes={},
        arguments={'content': 'x'},
    )
    assert verdict['decision'] == 'denied', verdict


@pytest.mark.asyncio
async def test_send_approval_card_delivers_card_to_owner_conversation(db, monkeypatch):
    """`_send_approval_card` 经 route_message 把 A 类审批卡片(content_type=5)发到 Agent↔主人会话。

    这是“发请求卡片消息”那一步的真实落库验证：卡片 schema 镜像 daemon card.rs，
    resource.type=agent_tool_approval + authorization_request.request_id 一致，主人点按钮才能
    走既有 emit_card_action → 云端 grant/deny 路径。
    """
    owner = await seed_human(db, nickname='挂起主人')
    agent_row = await seed_agent(db, owner_hasn_id=owner['hasn_id'], display_name='挂起分身')
    # 用「.begin() 会抛」的会话绑定：锁住发卡片必须走裸工厂会话（route_message 自带 commit），
    # 否则二次提交→发卡失败→工具被即时拒绝（线上事故根因，本断言防回归）。
    _bind_session_no_begin(monkeypatch, db)

    request_id = 'areq_test_card_delivery'
    ok = await ask_approval_gate._send_approval_card(
        request_id=request_id,
        agent_hasn_id=agent_row['hasn_id'],
        owner_hasn_id=owner['hasn_id'],
        required_scopes=_SCOPES,
        description='请求发布社区内容',
        capability_keys=[_TOOL, *_SCOPES],
    )
    assert ok is True, '卡片应真实投递成功（agent→其主人是透明可达路径）'

    row = (
        await db.execute(
            text(
                'SELECT content_type, content FROM hasn_messages '
                'WHERE from_id = :a AND to_id = :o AND content_type = 5 '
                'ORDER BY id DESC LIMIT 1'
            ),
            {'a': agent_row['hasn_id'], 'o': owner['hasn_id']},
        )
    ).first()
    assert row is not None, '应有一条 content_type=5 的卡片消息落到 Agent↔主人会话'
    content = row.content
    assert content['resource']['type'] == 'agent_tool_approval', content
    assert content['authorization_request']['request_id'] == request_id, content
    # 三按钮齐全（本次允许 / 总是允许 / 拒绝），主人才有完整裁决出口。
    action_ids = {content['primary_action']['action_id'], *(a['action_id'] for a in content['actions'])}
    assert {'grant_once', 'grant_always', 'deny'} <= action_ids, action_ids


@pytest.mark.asyncio
async def test_request_and_wait_returns_approved_when_owner_grants(db, monkeypatch):
    """主人批准 → request_and_wait 返回 approved（调用方据此放行真执行），DB 行落终态。"""
    owner = await seed_human(db, nickname='批准主人')
    agent_row = await seed_agent(db, owner_hasn_id=owner['hasn_id'], display_name='批准分身')
    _bind_session(monkeypatch, db)

    async def _grant_on_send(**kwargs):
        # 模拟主人收到卡片后立刻“本次允许”：daemon→云端 grant 把状态置 approved。
        await _set_status(db, kwargs['request_id'], 'approved')
        return True

    monkeypatch.setattr(ask_approval_gate, '_send_approval_card', _grant_on_send)

    verdict = await ask_approval_gate.request_and_wait(
        agent_hasn_id=agent_row['hasn_id'],
        owner_hasn_id=owner['hasn_id'],
        tool_name=_TOOL,
        required_scopes=_SCOPES,
        default_mode='ask',
        capability_modes={_TOOL: 'ask'},
        arguments={'content': '批准后才执行的帖子'},
    )
    assert verdict['decision'] == 'approved', verdict
    assert verdict['request_id'], verdict
    row = await _approval_row(db, verdict['request_id'])
    assert row is not None and row.status in ('approved', 'consumed'), row


@pytest.mark.asyncio
async def test_request_and_wait_returns_denied_when_owner_rejects(db, monkeypatch):
    """主人拒绝 → 返回 denied（调用方回工具错误，绝不放行）。"""
    owner = await seed_human(db, nickname='拒绝主人')
    agent_row = await seed_agent(db, owner_hasn_id=owner['hasn_id'], display_name='拒绝分身')
    _bind_session(monkeypatch, db)

    async def _deny_on_send(**kwargs):
        await _set_status(db, kwargs['request_id'], 'denied')
        return True

    monkeypatch.setattr(ask_approval_gate, '_send_approval_card', _deny_on_send)

    verdict = await ask_approval_gate.request_and_wait(
        agent_hasn_id=agent_row['hasn_id'],
        owner_hasn_id=owner['hasn_id'],
        tool_name=_TOOL,
        required_scopes=_SCOPES,
        default_mode='ask',
        capability_modes={_TOOL: 'ask'},
        arguments={'content': '会被拒绝的帖子'},
    )
    assert verdict['decision'] == 'denied', verdict


@pytest.mark.asyncio
async def test_request_and_wait_times_out_when_no_decision(db, monkeypatch):
    """窗口内主人未裁决 → timeout（调用方回超时错误），DB 行标 timeout。"""
    owner = await seed_human(db, nickname='超时主人')
    agent_row = await seed_agent(db, owner_hasn_id=owner['hasn_id'], display_name='超时分身')
    _bind_session(monkeypatch, db)

    async def _no_decision(**kwargs):
        return True  # 卡片发出但主人始终不点

    monkeypatch.setattr(ask_approval_gate, '_send_approval_card', _no_decision)
    # 把挂起窗口收成 0：deadline≈now，轮询不进循环即超时（不真正等 10 分钟）。
    monkeypatch.setattr(ask_gate_module, 'CLOUD_WAIT_SECONDS', 0)

    verdict = await ask_approval_gate.request_and_wait(
        agent_hasn_id=agent_row['hasn_id'],
        owner_hasn_id=owner['hasn_id'],
        tool_name=_TOOL,
        required_scopes=_SCOPES,
        default_mode='ask',
        capability_modes={_TOOL: 'ask'},
        arguments={'content': '无人裁决的帖子'},
    )
    assert verdict['decision'] == 'timeout', verdict
    row = await _approval_row(db, verdict['request_id'])
    assert row is not None and row.status == 'timeout', row


@pytest.mark.asyncio
async def test_request_and_wait_denies_when_card_delivery_fails(db, monkeypatch):
    """发不出卡片 = 没有审批出口 → denied（零 fake：不能假装在等），DB 行标 timeout。"""
    owner = await seed_human(db, nickname='发卡失败主人')
    agent_row = await seed_agent(db, owner_hasn_id=owner['hasn_id'], display_name='发卡失败分身')
    _bind_session(monkeypatch, db)

    async def _send_fails(**kwargs):
        return False

    monkeypatch.setattr(ask_approval_gate, '_send_approval_card', _send_fails)

    verdict = await ask_approval_gate.request_and_wait(
        agent_hasn_id=agent_row['hasn_id'],
        owner_hasn_id=owner['hasn_id'],
        tool_name=_TOOL,
        required_scopes=_SCOPES,
        default_mode='ask',
        capability_modes={_TOOL: 'ask'},
        arguments={'content': '卡片发不出去的帖子'},
    )
    assert verdict['decision'] == 'denied', verdict
    row = await _approval_row(db, verdict['request_id'])
    assert row is not None and row.status == 'timeout', row


@pytest.mark.asyncio
async def test_grant_authorizes_by_approval_owner_even_if_agent_owner_is_stale(db, monkeypatch):
    """cloud-pend 点「本次允许」403 根因修复：裁决授权按**审批行 owner**，不按 agent.owner_id。

    复现用户场景：分身的 hasn_agents.owner_id 与当前主人不一致（身份迁移/历史脏数据），
    但这条审批就是这位主人触发并送达的——主人理应能批准（旧逻辑据 agent.owner_id 误判 403）。
    同时验证另一主人仍不能批准（授权不放松，仍绑定到该审批所属主人）。
    """
    from backend.app.hasn.service.agent_scopes_service import agent_scopes_service

    owner = await seed_human(db, nickname='审批主人')
    other = await seed_human(db, nickname='路人甲')
    agent_row = await seed_agent(db, owner_hasn_id=owner['hasn_id'], display_name='被审批分身')
    _bind_session(monkeypatch, db)

    env = await ask_approval_gate.open_request(
        agent_hasn_id=agent_row['hasn_id'],
        owner_hasn_id=owner['hasn_id'],
        tool_name=_TOOL,
        required_scopes=_SCOPES,
        default_mode='allow',
        capability_modes={_TOOL: 'ask'},
        arguments={'content': '需要主人批准的帖子'},
    )
    request_id = env['approval']['request_id']

    # 模拟脏数据：把 agent.owner_id 改成与当前主人不一致（旧逻辑会据此 403）。
    await db.execute(
        text('UPDATE hasn_agents SET owner_id = :x WHERE hasn_id = :a'),
        {'x': 'h_stale_owner_xxx', 'a': agent_row['hasn_id']},
    )

    # 另一主人不能批准（pending 行 + owner 不符 → 403），证明授权未放松。
    with pytest.raises(errors.ForbiddenError):
        await agent_scopes_service.grant_approval(
            db=db,
            agent_hasn_id=agent_row['hasn_id'],
            owner_hasn_id=other['hasn_id'],
            request_id=request_id,
            scope='once',
        )

    # 审批所属主人仍可批准（即便 agent.owner_id 已脏）——这正是 cloud-pend 403 的修复。
    result = await agent_scopes_service.grant_approval(
        db=db,
        agent_hasn_id=agent_row['hasn_id'],
        owner_hasn_id=owner['hasn_id'],
        request_id=request_id,
        scope='once',
    )
    assert result['grant_scope'] == 'once', result
    row = await _approval_row(db, request_id)
    assert row is not None and row.status == 'approved', row
