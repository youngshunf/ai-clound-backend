"""Agent MCP 接入凭证签发/吊销/校验集成测试（P1）。

连真实本地 PostgreSQL（127.0.0.1:15432/huanxing），savepoint 事务隔离，
结束整体回滚不留痕（符合"零 Mock 零 Fake"：连真库但不污染）。

验收（设计文档 12-Agent接入凭证设计.md §10 P1）：
- 签发返回完整明文 key 一次，库内只存 SHA-256 哈希（无明文列）；
- 吊销即时失效（吊销后再校验该 key 必拒）。
"""

from __future__ import annotations

import contextlib

import pytest
import pytest_asyncio

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.pool import NullPool

from backend.app.hasn.crud.crud_hasn_agent_mcp_keys import hasn_agent_mcp_keys_dao
from backend.app.hasn.schema.hasn_agent_mcp_keys import IssueAgentMcpKeyParam
from backend.app.hasn.service.hasn_agent_mcp_keys_service import KEY_PREFIX, hasn_agent_mcp_keys_service
from backend.common.security.encryption import key_encryption
from backend.common.exception import errors
from backend.database.db import uuid4_str

# 本地开发数据库（与 tests/hasn/conftest.py 同源，刻意不依赖 .env，避免 worktree 落到 5432）
ASYNC_DATABASE_URL = 'postgresql+psycopg://mac@127.0.0.1:15432/huanxing'


@pytest_asyncio.fixture
async def db() -> AsyncSession:
    """事务隔离的 AsyncSession（用例结束自动回滚，绝不污染真库）。"""
    engine = create_async_engine(ASYNC_DATABASE_URL, poolclass=NullPool)
    conn = await engine.connect()
    trans = await conn.begin()
    session = AsyncSession(
        bind=conn,
        expire_on_commit=False,
        join_transaction_mode='create_savepoint',
    )
    try:
        yield session
    finally:
        await session.close()
        await trans.rollback()
        await conn.close()
        await engine.dispose()


async def _seed_user(db: AsyncSession, *, nickname: str) -> int:
    """落一个真实 sys_user（owner_user_id 外键依赖它），返回自增 id。"""
    uname = f'mcpkey_{uuid4_str()[:12]}'
    uid = (
        await db.execute(
            text(
                'INSERT INTO sys_user (uuid, username, nickname, status, is_superuser, is_staff, '
                'is_multi_login, join_time, created_time) '
                'VALUES (:uuid, :username, :nickname, 1, false, false, false, now(), now()) '
                'RETURNING id'
            ),
            {'uuid': uuid4_str(), 'username': uname, 'nickname': nickname},
        )
    ).scalar_one()
    await db.flush()
    return int(uid)


async def _seed_human(db: AsyncSession, *, nickname: str = 'MCPKey主人') -> tuple[str, int]:
    uid = await _seed_user(db, nickname=nickname)
    hasn_id = f'h_{uuid4_str()[:20]}'
    star_id = f's_{uuid4_str()[:12]}'
    await db.execute(
        text(
            'INSERT INTO hasn_humans (hasn_id, star_id, user_id, nickname, avatar, bio, status, '
            'contact_policy, tags, stats, created_time, updated_time) '
            "VALUES (:hasn_id, :star_id, :uid, :nickname, '', '', 'active', "
            "'{}'::jsonb, ARRAY[]::varchar[], '{}'::jsonb, now(), now())"
        ),
        {'hasn_id': hasn_id, 'star_id': star_id, 'uid': uid, 'nickname': nickname},
    )
    await db.flush()
    return hasn_id, uid


async def _seed_agent(db: AsyncSession, *, owner_id: str, display_name: str = '分身') -> str:
    hasn_id = f'a_{uuid4_str()[:20]}'
    star_id = f's_{uuid4_str()[:12]}'
    await db.execute(
        text(
            'INSERT INTO hasn_agents (hasn_id, star_id, owner_id, agent_name, display_name, '
            'api_key_hash, created_time) '
            'VALUES (:hasn_id, :star_id, :owner_id, :agent_name, :display_name, :api_key_hash, now())'
        ),
        {
            'hasn_id': hasn_id,
            'star_id': star_id,
            'owner_id': owner_id,
            'agent_name': display_name[:30],
            'display_name': display_name,
            'api_key_hash': uuid4_str().replace('-', '')[:64],
        },
    )
    await db.flush()
    return hasn_id


@pytest.mark.asyncio
async def test_issue_returns_plaintext_once_and_stores_only_hash(db: AsyncSession) -> None:
    """签发返回完整明文 key 一次；库内只存哈希，不存明文，哈希=SHA-256(明文)。"""
    owner_id, uid = await _seed_human(db)
    agent_id = await _seed_agent(db, owner_id=owner_id)

    issued = await hasn_agent_mcp_keys_service.issue(
        db,
        obj=IssueAgentMcpKeyParam(agent_hasn_id=agent_id, scopes=['hasn.memory.read']),
        owner_hasn_id=owner_id,
        owner_user_id=uid,
    )

    # 明文一次：返回完整 key，带正确前缀
    assert issued.key.startswith(f'{KEY_PREFIX}_'), issued.key
    assert issued.key_prefix == issued.key[:16]
    assert issued.status == 'active'
    # MCP Key 只承载身份；旧 scopes 入参为兼容占位，签发结果恒为空。
    assert issued.scopes == []

    # 库存哈希：DB 行的 key_hash 等于 SHA-256(明文)，且行上不存在明文列
    row = await hasn_agent_mcp_keys_dao.get(db, issued.id)
    assert row is not None
    assert row.key_hash == key_encryption.hash_key(issued.key)
    assert row.key_hash != issued.key  # 哈希 != 明文
    assert not hasattr(row, 'key'), 'ORM 行不得持有明文 key 列'
    assert not hasattr(row, 'key_encrypted'), '本表不可逆，不应有可解密密文列'


@pytest.mark.asyncio
async def test_verify_active_then_revoke_invalidates(db: AsyncSession) -> None:
    """active key 校验通过；吊销后再校验同一 key 必拒（吊销即失效）。"""
    owner_id, uid = await _seed_human(db)
    agent_id = await _seed_agent(db, owner_id=owner_id)
    issued = await hasn_agent_mcp_keys_service.issue(
        db,
        obj=IssueAgentMcpKeyParam(agent_hasn_id=agent_id),
        owner_hasn_id=owner_id,
        owner_user_id=uid,
    )

    # 吊销前：校验命中，返回对应行
    record = await hasn_agent_mcp_keys_service.verify(db, presented_key=issued.key)
    assert record.id == issued.id
    assert record.agent_hasn_id == agent_id

    # 吊销
    await hasn_agent_mcp_keys_service.revoke(db, pk=issued.id, owner_hasn_id=owner_id)
    revoked = await hasn_agent_mcp_keys_dao.get(db, issued.id)
    assert revoked is not None and revoked.status == 'revoked'

    # 吊销后：同一 key 校验被拒
    with pytest.raises(errors.AuthorizationError):
        await hasn_agent_mcp_keys_service.verify(db, presented_key=issued.key)


@pytest.mark.asyncio
async def test_issue_forbidden_for_unowned_agent(db: AsyncSession) -> None:
    """不能为不属于自己的 Agent 签发凭证（HASN：所有 Agent 必须有主人）。"""
    owner_a, _ = await _seed_human(db, nickname='ownerA')
    owner_b, uid_b = await _seed_human(db, nickname='ownerB')
    agent_of_a = await _seed_agent(db, owner_id=owner_a)

    with pytest.raises(errors.ForbiddenError):
        await hasn_agent_mcp_keys_service.issue(
            db,
            obj=IssueAgentMcpKeyParam(agent_hasn_id=agent_of_a),
            owner_hasn_id=owner_b,
            owner_user_id=uid_b,
        )


@pytest.mark.asyncio
async def test_node_binding_enforced(db: AsyncSession) -> None:
    """node 绑定默认开：错 node 拒、对 node 通过；不绑定（None）则任意 node 通过。"""
    owner_id, uid = await _seed_human(db)
    agent_id = await _seed_agent(db, owner_id=owner_id)

    bound = await hasn_agent_mcp_keys_service.issue(
        db,
        obj=IssueAgentMcpKeyParam(agent_hasn_id=agent_id, node_id='node-A'),
        owner_hasn_id=owner_id,
        owner_user_id=uid,
    )
    with pytest.raises(errors.AuthorizationError):
        await hasn_agent_mcp_keys_service.verify(db, presented_key=bound.key, node_id='node-B')
    ok = await hasn_agent_mcp_keys_service.verify(db, presented_key=bound.key, node_id='node-A')
    assert ok.id == bound.id

    unbound = await hasn_agent_mcp_keys_service.issue(
        db,
        obj=IssueAgentMcpKeyParam(agent_hasn_id=agent_id),
        owner_hasn_id=owner_id,
        owner_user_id=uid,
    )
    any_node = await hasn_agent_mcp_keys_service.verify(db, presented_key=unbound.key, node_id='whatever')
    assert any_node.id == unbound.id


@pytest.mark.asyncio
async def test_verify_unknown_key_rejected(db: AsyncSession) -> None:
    """未知 / 空 key 一律拒（零 fake：不静默放过）。"""
    with pytest.raises(errors.AuthorizationError):
        await hasn_agent_mcp_keys_service.verify(db, presented_key=f'{KEY_PREFIX}_bogusbogusbogus')
    with pytest.raises(errors.AuthorizationError):
        await hasn_agent_mcp_keys_service.verify(db, presented_key='')


# ============================================================================
# P2：云端 MCP 双路鉴权（streamable.py 前缀分流 + key→AgentContext）
# 把 _authenticate_with_key 内部的 async_db_session 重定向到本测试的事务隔离
# session（同一真库、同一事务，绝非 fake），用例结束随 fixture 回滚不留痕。
# ============================================================================

from backend.app.mcp.streamable import HasnMcpStreamableServer  # noqa: E402


def _headers(token: str, *, node_id: str | None = None, agent_id: str | None = None) -> dict[bytes, bytes]:
    h: dict[bytes, bytes] = {b'authorization': f'Bearer {token}'.encode()}
    if node_id is not None:
        h[b'x-node-id'] = node_id.encode()
    if agent_id is not None:
        h[b'x-hasn-agent-id'] = agent_id.encode()
    return h


@pytest.fixture
def server(db: AsyncSession, monkeypatch) -> HasnMcpStreamableServer:
    """鉴权方法内部的 async_db_session() 重定向到测试事务 session。"""

    @contextlib.asynccontextmanager
    async def _yield_test_session():
        yield db  # 不 commit / 不 close —— 由 db fixture 统一回滚

    monkeypatch.setattr('backend.app.mcp.streamable.async_db_session', lambda: _yield_test_session())
    return HasnMcpStreamableServer()


@pytest.mark.asyncio
async def test_key_auth_self_identifies_without_agent_id_header(
    db: AsyncSession, server: HasnMcpStreamableServer
) -> None:
    """key 自识别身份：无 X-HASN-Agent-ID 也能解析出正确 AgentContext + 合成稳定 session_uuid。"""
    owner_id, uid = await _seed_human(db)
    agent_id = await _seed_agent(db, owner_id=owner_id, display_name='星诺')
    issued = await hasn_agent_mcp_keys_service.issue(
        db,
        obj=IssueAgentMcpKeyParam(agent_hasn_id=agent_id, scopes=['hasn.memory.read', 'hasn.memory.write']),
        owner_hasn_id=owner_id,
        owner_user_id=uid,
    )
    await db.execute(
        text(
            'INSERT INTO hasn_agent_scopes '
            '(agent_hasn_id, owner_hasn_id, default_mode, capability_modes, updated_time) '
            "VALUES (:agent_id, :owner_id, 'ask', "
            "CAST('{\"hasn.memory.read\":\"allow\",\"hasn.memory.write\":\"deny\"}' AS jsonb), now())"
        ),
        {'agent_id': agent_id, 'owner_id': owner_id},
    )
    await db.flush()

    ctx = await server._authenticate_from_headers(_headers(issued.key))

    assert ctx.hasn_id == agent_id
    assert ctx.owner_hasn_id == owner_id
    assert ctx.owner_id == uid
    assert not hasattr(ctx, 'scopes')
    assert ctx.default_mode == 'ask'
    assert ctx.capability_modes == {
        'hasn.memory.read': 'allow',
        'hasn.memory.write': 'deny',
    }
    assert ctx.agent_status == 'active'
    # 合成稳定标识：amk_<key_id>，与 JWT 会话可区分
    assert ctx.session_uuid == f'amk_{issued.id}'
    # to_token_payload() 不再因缺 session_uuid 抛错（设计 §11 解法生效）
    payload = ctx.to_token_payload()
    assert payload.session_uuid == f'amk_{issued.id}'
    assert payload.agent_hasn_id == agent_id
    assert payload.owner_user_id == uid


@pytest.mark.asyncio
async def test_key_auth_rejects_after_revoke(db: AsyncSession, server: HasnMcpStreamableServer) -> None:
    """吊销后用 key 连云端 MCP 鉴权被拒（转 ValueError→401）。"""
    owner_id, uid = await _seed_human(db)
    agent_id = await _seed_agent(db, owner_id=owner_id)
    issued = await hasn_agent_mcp_keys_service.issue(
        db, obj=IssueAgentMcpKeyParam(agent_hasn_id=agent_id), owner_hasn_id=owner_id, owner_user_id=uid
    )
    await hasn_agent_mcp_keys_service.revoke(db, pk=issued.id, owner_hasn_id=owner_id)

    with pytest.raises(ValueError):
        await server._authenticate_from_headers(_headers(issued.key))


@pytest.mark.asyncio
async def test_key_auth_node_binding(db: AsyncSession, server: HasnMcpStreamableServer) -> None:
    """node 绑定：错 X-Node-Id 拒、对 X-Node-Id 通过。"""
    owner_id, uid = await _seed_human(db)
    agent_id = await _seed_agent(db, owner_id=owner_id)
    issued = await hasn_agent_mcp_keys_service.issue(
        db,
        obj=IssueAgentMcpKeyParam(agent_hasn_id=agent_id, node_id='node-X'),
        owner_hasn_id=owner_id,
        owner_user_id=uid,
    )

    with pytest.raises(ValueError):
        await server._authenticate_from_headers(_headers(issued.key, node_id='node-Y'))

    ctx = await server._authenticate_from_headers(_headers(issued.key, node_id='node-X'))
    assert ctx.hasn_id == agent_id


@pytest.mark.asyncio
async def test_key_auth_defensive_agent_id_mismatch(
    db: AsyncSession, server: HasnMcpStreamableServer
) -> None:
    """带了 X-HASN-Agent-ID 时做防御性一致性核对：不一致拒。"""
    owner_id, uid = await _seed_human(db)
    agent_id = await _seed_agent(db, owner_id=owner_id)
    issued = await hasn_agent_mcp_keys_service.issue(
        db, obj=IssueAgentMcpKeyParam(agent_hasn_id=agent_id), owner_hasn_id=owner_id, owner_user_id=uid
    )

    with pytest.raises(ValueError):
        await server._authenticate_from_headers(_headers(issued.key, agent_id='a_someone_else'))

    # 一致则放行
    ctx = await server._authenticate_from_headers(_headers(issued.key, agent_id=agent_id))
    assert ctx.hasn_id == agent_id


@pytest.mark.asyncio
async def test_non_key_token_routes_to_jwt_path(db: AsyncSession, server: HasnMcpStreamableServer) -> None:
    """非 hasn_amk_ 前缀 token 路由到 JWT 兼容路：缺 X-HASN-Agent-ID 报缺头，带了则 JWT 校验失败。"""
    # 无 agent-id 头 → JWT 路要求该头
    with pytest.raises(ValueError, match='X-HASN-Agent-ID'):
        await server._authenticate_from_headers(_headers('eyJ.bogus.jwt'))
    # 带 agent-id 头 → 进 verify_agent_token，假 token 校验失败
    with pytest.raises(ValueError):
        await server._authenticate_from_headers(_headers('eyJ.bogus.jwt', agent_id='a_x'))
