"""会议副驾（潜行会议副驾）云端数据底座 copilot_service 真实 PG 测试（零 mock，P2）。

覆盖（设计事实源 §8.4.2 / §8.5）：
- 建会话（upsert 新建）+ 默认值
- 绑定/改绑 owner 校验（拒绝别 owner 的分身）
- bind-only-if-unbound：首次绑定回写 preference.default_agent_id
- session_id upsert 幂等（同 session_id 二次调用更新而非重复插入）
- 后续新会话默认取 preference.default_agent_id
- projection 回填（+ 结束置 ended）
- response_mode：改 session 不回写 preference.default（会内临时 vs 长效默认独立）
- owner 数据隔离（A owner 看不到 / 取不到 B owner 会话）

用真实本地 PostgreSQL（不可达则 skip）：插入隔离测试行 → flush（不 commit）→ 断言 → rollback。
"""

from __future__ import annotations

import uuid

import pytest
import pytest_asyncio

from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from backend.app.hasn.model import HasnAgents
from backend.app.hasn_copilot.model import CopilotPreference, CopilotSession
from backend.app.hasn_copilot.service.copilot_service import copilot_service
from backend.common.exception import errors
from backend.database.db import SQLALCHEMY_DATABASE_URL

pytestmark = pytest.mark.asyncio


@pytest_asyncio.fixture
async def session():
    engine = create_async_engine(SQLALCHEMY_DATABASE_URL, poolclass=NullPool)
    try:
        async with engine.connect() as conn:
            await conn.execute(select(1))
    except Exception as exc:
        await engine.dispose()
        pytest.skip(f'本地 PostgreSQL 不可达，跳过: {exc!r}')

    sess = async_sessionmaker(engine, expire_on_commit=False)()
    try:
        yield sess
    finally:
        await sess.rollback()
        await sess.close()
        await engine.dispose()


async def _seed_agent(session, *, owner_id: str, tag: str) -> str:
    """为 owner 插入一个名下分身，返回 agent hasn_id。

    star_id 有全局 UNIQUE 约束，须每个分身唯一（否则空串 '' 撞 hasn_agents_star_id_key）。
    """
    agent_id = f'a_{tag}'
    session.add(
        HasnAgents(
            hasn_id=agent_id,
            owner_id=owner_id,
            star_id=f'star_{tag}',
            display_name=f'分身-{tag}',
        )
    )
    await session.flush()
    return agent_id


async def test_upsert_creates_session_with_defaults(session) -> None:
    tag = uuid.uuid4().hex[:8]
    owner = f'h_owner_{tag}'
    sid = f'sess_{tag}'

    data = await copilot_service.upsert_session(session, owner_hasn_id=owner, session_id=sid, title='周会')

    assert data['session_id'] == sid
    assert data['owner_hasn_id'] == owner
    assert data['title'] == '周会'
    assert data['scene'] == 'meeting'  # 默认
    assert data['response_mode'] == 'manual'  # 默认
    assert data['status'] == 'active'  # 默认
    assert data['source_config'] == {}
    assert data['bound_agent_id'] is None  # 未绑定（owner 还没默认分身）
    assert data['projection_conversation_id'] is None
    assert isinstance(data['id'], int)


async def test_bind_agent_owner_validation_rejects_foreign_agent(session) -> None:
    tag = uuid.uuid4().hex[:8]
    owner_a = f'h_owner_a_{tag}'
    owner_b = f'h_owner_b_{tag}'
    # 分身归属 B
    agent_b = await _seed_agent(session, owner_id=owner_b, tag=f'b_{tag}')

    # A 想把 B 的分身绑到自己的会话 → 必须被拒
    with pytest.raises(errors.NotFoundError):
        await copilot_service.upsert_session(
            session, owner_hasn_id=owner_a, session_id=f'sess_{tag}', bound_agent_id=agent_b
        )


async def test_bind_only_if_unbound_writes_back_default(session) -> None:
    """首次绑定：选定分身后回写 preference.default_agent_id（§8.5.1）。"""
    tag = uuid.uuid4().hex[:8]
    owner = f'h_owner_{tag}'
    agent = await _seed_agent(session, owner_id=owner, tag=f'own_{tag}')

    # 绑定前无 preference 行 → 默认分身为空
    pref_before = await copilot_service.get_preference(session, owner_hasn_id=owner)
    assert pref_before['default_agent_id'] is None

    data = await copilot_service.upsert_session(
        session, owner_hasn_id=owner, session_id=f'sess_{tag}', bound_agent_id=agent
    )
    assert data['bound_agent_id'] == agent

    # 首次绑定回写为 owner 默认
    pref_after = await copilot_service.get_preference(session, owner_hasn_id=owner)
    assert pref_after['default_agent_id'] == agent


async def test_subsequent_session_defaults_to_preference_agent(session) -> None:
    """有默认分身后，新会话不传 bound_agent_id 也默认取 preference.default_agent_id。"""
    tag = uuid.uuid4().hex[:8]
    owner = f'h_owner_{tag}'
    agent = await _seed_agent(session, owner_id=owner, tag=f'own_{tag}')

    # 第一场绑定 → 写默认
    await copilot_service.upsert_session(session, owner_hasn_id=owner, session_id=f'sess1_{tag}', bound_agent_id=agent)
    # 第二场不传 bound_agent_id → 应默认取 agent
    data2 = await copilot_service.upsert_session(session, owner_hasn_id=owner, session_id=f'sess2_{tag}')
    assert data2['bound_agent_id'] == agent


async def test_session_id_upsert_is_idempotent(session) -> None:
    """同 session_id 二次调用 = 更新而非重复插入（离线起会联网补登幂等）。"""
    tag = uuid.uuid4().hex[:8]
    owner = f'h_owner_{tag}'
    sid = f'sess_{tag}'

    d1 = await copilot_service.upsert_session(session, owner_hasn_id=owner, session_id=sid, title='初稿')
    d2 = await copilot_service.upsert_session(
        session, owner_hasn_id=owner, session_id=sid, title='更新后', status='ended'
    )

    assert d2['id'] == d1['id']  # 同一行
    assert d2['title'] == '更新后'
    assert d2['status'] == 'ended'

    # DB 里该 (owner, session_id) 只有一行
    rows = (
        (
            await session.execute(
                select(CopilotSession).where(CopilotSession.owner_hasn_id == owner, CopilotSession.session_id == sid)
            )
        )
        .scalars()
        .all()
    )
    assert len(rows) == 1


async def test_set_projection_backfills_and_ends(session) -> None:
    tag = uuid.uuid4().hex[:8]
    owner = f'h_owner_{tag}'
    sid = f'sess_{tag}'
    await copilot_service.upsert_session(session, owner_hasn_id=owner, session_id=sid)

    conv_id = str(uuid.uuid4())
    msg_id = str(uuid.uuid4())
    data = await copilot_service.set_projection(
        session,
        owner_hasn_id=owner,
        session_id=sid,
        projection_conversation_id=conv_id,
        projection_message_id=msg_id,
    )
    assert data['projection_conversation_id'] == conv_id
    assert data['projection_message_id'] == msg_id
    assert data['status'] == 'ended'
    assert data['ended_time'] is not None


async def test_response_mode_session_does_not_leak_to_preference(session) -> None:
    """改 session.response_mode 仅本场，**不回写** preference.default_response_mode（§8.4.2）。"""
    tag = uuid.uuid4().hex[:8]
    owner = f'h_owner_{tag}'
    sid = f'sess_{tag}'
    await copilot_service.upsert_session(session, owner_hasn_id=owner, session_id=sid)

    # 会内临时切「仅转写」
    updated = await copilot_service.update_session(
        session, owner_hasn_id=owner, session_id=sid, response_mode='transcribe_only'
    )
    assert updated['response_mode'] == 'transcribe_only'

    # owner 默认应仍是 manual（未被会内临时切污染）
    pref = await copilot_service.get_preference(session, owner_hasn_id=owner)
    assert pref['default_response_mode'] == 'manual'


async def test_update_preference_changes_default_mode(session) -> None:
    """改 preference.default_response_mode = 改今后默认（与会内临时切独立）。"""
    tag = uuid.uuid4().hex[:8]
    owner = f'h_owner_{tag}'
    pref = await copilot_service.update_preference(
        session, owner_hasn_id=owner, default_response_mode='auto', auto_summary=False
    )
    assert pref['default_response_mode'] == 'auto'
    assert pref['auto_summary'] is False

    # 之后新会话默认 response_mode 取 owner 默认 auto
    data = await copilot_service.upsert_session(session, owner_hasn_id=owner, session_id=f'sess_{tag}')
    assert data['response_mode'] == 'auto'


async def test_rebind_default_agent_validates_owner(session) -> None:
    tag = uuid.uuid4().hex[:8]
    owner_a = f'h_owner_a_{tag}'
    owner_b = f'h_owner_b_{tag}'
    agent_a = await _seed_agent(session, owner_id=owner_a, tag=f'a_{tag}')
    agent_b = await _seed_agent(session, owner_id=owner_b, tag=f'b_{tag}')

    # 改绑自己的分身 OK
    res = await copilot_service.rebind_default_agent(session, owner_hasn_id=owner_a, agent_id=agent_a)
    assert res['preference']['default_agent_id'] == agent_a

    # 改绑别 owner 的分身被拒
    with pytest.raises(errors.NotFoundError):
        await copilot_service.rebind_default_agent(session, owner_hasn_id=owner_a, agent_id=agent_b)


async def test_rebind_also_updates_session_bound(session) -> None:
    tag = uuid.uuid4().hex[:8]
    owner = f'h_owner_{tag}'
    sid = f'sess_{tag}'
    agent1 = await _seed_agent(session, owner_id=owner, tag=f'one_{tag}')
    agent2 = await _seed_agent(session, owner_id=owner, tag=f'two_{tag}')
    await copilot_service.upsert_session(session, owner_hasn_id=owner, session_id=sid, bound_agent_id=agent1)

    res = await copilot_service.rebind_default_agent(session, owner_hasn_id=owner, agent_id=agent2, also_session_id=sid)
    assert res['preference']['default_agent_id'] == agent2
    assert res['session']['bound_agent_id'] == agent2  # 同时改了本场


async def test_owner_isolation_get_and_list(session) -> None:
    """owner 数据隔离：A 取不到 / 列不到 B owner 的会话。"""
    tag = uuid.uuid4().hex[:8]
    owner_a = f'h_owner_a_{tag}'
    owner_b = f'h_owner_b_{tag}'
    sid_b = f'sess_b_{tag}'
    await copilot_service.upsert_session(session, owner_hasn_id=owner_b, session_id=sid_b, title='B 的会议')

    # A 直接按 B 的 session_id 取 → 不存在（隔离边界，不泄露）
    with pytest.raises(errors.NotFoundError):
        await copilot_service.get_session(session, owner_hasn_id=owner_a, session_id=sid_b)

    # A 的列表里看不到 B 的会话
    listing = await copilot_service.list_sessions(session, owner_hasn_id=owner_a)
    sids = {item['session_id'] for item in listing['items']}
    assert sid_b not in sids

    # B 自己能取到、能列到
    got = await copilot_service.get_session(session, owner_hasn_id=owner_b, session_id=sid_b)
    assert got['title'] == 'B 的会议'
    listing_b = await copilot_service.list_sessions(session, owner_hasn_id=owner_b)
    assert sid_b in {item['session_id'] for item in listing_b['items']}


async def test_same_session_id_different_owner_are_distinct_rows(session) -> None:
    """同一 session_id 字符串在不同 owner 下是不同会话（upsert 受 owner 约束，不串行）。

    注：DDL 的 idx_copilot_session_sid 是全局 UNIQUE(session_id)，真实 daemon 生成的
    session_id 是 ULID 天然全局唯一；本测试用不同 session_id 验证 owner 维度的取数隔离，
    避免触碰全局唯一约束。
    """
    tag = uuid.uuid4().hex[:8]
    owner_a = f'h_owner_a_{tag}'
    owner_b = f'h_owner_b_{tag}'
    a = await copilot_service.upsert_session(session, owner_hasn_id=owner_a, session_id=f'sess_a_{tag}')
    b = await copilot_service.upsert_session(session, owner_hasn_id=owner_b, session_id=f'sess_b_{tag}')
    assert a['id'] != b['id']
    assert a['owner_hasn_id'] != b['owner_hasn_id']


async def test_invalid_response_mode_and_scene_rejected(session) -> None:
    tag = uuid.uuid4().hex[:8]
    owner = f'h_owner_{tag}'
    with pytest.raises(errors.RequestError):
        await copilot_service.upsert_session(
            session, owner_hasn_id=owner, session_id=f'sess_{tag}', response_mode='bogus'
        )
    with pytest.raises(errors.RequestError):
        await copilot_service.upsert_session(session, owner_hasn_id=owner, session_id=f'sess2_{tag}', scene='party')


async def test_preference_single_row_per_owner(session) -> None:
    """preference 是 per-owner 单行：多次 update 仍只一行。"""
    tag = uuid.uuid4().hex[:8]
    owner = f'h_owner_{tag}'
    await copilot_service.update_preference(session, owner_hasn_id=owner, default_response_mode='auto')
    await copilot_service.update_preference(session, owner_hasn_id=owner, auto_summary=False)

    rows = (
        (await session.execute(select(CopilotPreference).where(CopilotPreference.owner_hasn_id == owner)))
        .scalars()
        .all()
    )
    assert len(rows) == 1
    assert rows[0].default_response_mode == 'auto'
    assert rows[0].auto_summary is False
