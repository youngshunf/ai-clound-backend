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
    _bind_session(monkeypatch, db)

    request_id = 'areq_test_card_delivery'
    ok = await ask_approval_gate._send_approval_card(
        request_id=request_id,
        agent_hasn_id=agent_row['hasn_id'],
        owner_hasn_id=owner['hasn_id'],
        tool_name=_TOOL,
        description='请求执行【发布社区帖子】',
        args_digest={'content': '一条需要主人批准的帖子'},
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
