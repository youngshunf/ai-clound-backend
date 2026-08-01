"""节点访问票据与会话授予的一次性语义（H4 · 契约 §3.4）——零 mock，打真实 Redis。

覆盖：票据签发→核销→重放拒；grant 签发→校验→jti 重放拒；签名/exp/typ 篡改拒。
"""

from __future__ import annotations

import time

import jwt
import pytest
import pytest_asyncio

from backend.app.hasn_hosting.constants import (
    ERR_GRANT_INVALID,
    ERR_GRANT_REPLAYED,
    GRANT_ALGORITHM,
    GRANT_AUDIENCE,
    GRANT_ISSUER,
)
from backend.app.hasn_hosting.service.access_ticket_service import access_ticket_service
from backend.core.conf import settings
from backend.database.redis import redis_client

pytestmark = pytest.mark.asyncio

USER_ID = 990202
OWNER = 'h_hosting_ticket_owner'
NODE_ID = 'n_cloud_test_ticket_0001'
HOST = 'hosting-test'


@pytest_asyncio.fixture(autouse=True)
async def _require_redis() -> None:
    try:
        await redis_client.ping()
    except Exception as exc:  # noqa: BLE001
        pytest.skip(f'本地 Redis 不可达，跳过: {exc!r}')


# ── 票据 ──


async def test_ticket_redeem_is_single_use() -> None:
    issued = await access_ticket_service.issue_ticket(
        user_id=USER_ID, owner_hasn_id=OWNER, node_id=NODE_ID, host=HOST
    )
    assert issued.ticket
    assert issued.expires_at

    payload = await access_ticket_service.redeem_ticket(issued.ticket)
    assert payload is not None
    assert payload.user_id == USER_ID
    assert payload.owner_hasn_id == OWNER
    assert payload.node_id == NODE_ID
    assert payload.host == HOST

    # 第二次核销必须落空——原子 GETDEL 保证没有并发双取窗口
    assert await access_ticket_service.redeem_ticket(issued.ticket) is None


async def test_unknown_ticket_redeems_to_none() -> None:
    assert await access_ticket_service.redeem_ticket('not-a-real-ticket-value') is None


async def test_ticket_key_stores_hash_not_plaintext() -> None:
    """Redis 里落的是 sha256(ticket)，读到 Redis 也换不出明文票据。"""
    issued = await access_ticket_service.issue_ticket(
        user_id=USER_ID, owner_hasn_id=OWNER, node_id=NODE_ID, host=HOST
    )
    try:
        assert await redis_client.exists(f'hasn:node_access_ticket:{issued.ticket}') == 0
    finally:
        await access_ticket_service.redeem_ticket(issued.ticket)


# ── 会话授予 grant ──


async def test_grant_verify_then_replay_is_rejected() -> None:
    grant = access_ticket_service.sign_grant(node_id=NODE_ID, owner_hasn_id=OWNER, user_id=USER_ID)

    claims, error = await access_ticket_service.verify_grant(grant)
    assert error is None
    assert claims is not None
    assert (claims.node_id, claims.owner_hasn_id, claims.user_id) == (NODE_ID, OWNER, USER_ID)

    claims2, error2 = await access_ticket_service.verify_grant(grant)
    assert claims2 is None
    assert error2 == ERR_GRANT_REPLAYED


async def test_grant_with_wrong_signature_is_invalid() -> None:
    forged = jwt.encode(
        {
            'iss': GRANT_ISSUER,
            'aud': GRANT_AUDIENCE,
            'typ': 'node_session_grant',
            'node_id': NODE_ID,
            'owner_hasn_id': OWNER,
            'user_id': USER_ID,
            'jti': 'forged-jti-0001',
            'exp': int(time.time()) + 60,
        },
        'a-completely-different-secret',
        algorithm=GRANT_ALGORITHM,
    )
    claims, error = await access_ticket_service.verify_grant(forged)
    assert claims is None
    assert error == ERR_GRANT_INVALID


async def test_expired_grant_is_invalid() -> None:
    expired = jwt.encode(
        {
            'iss': GRANT_ISSUER,
            'aud': GRANT_AUDIENCE,
            'typ': 'node_session_grant',
            'node_id': NODE_ID,
            'owner_hasn_id': OWNER,
            'user_id': USER_ID,
            'jti': 'expired-jti-0001',
            'exp': int(time.time()) - 5,
        },
        settings.TOKEN_SECRET_KEY,
        algorithm=GRANT_ALGORITHM,
    )
    claims, error = await access_ticket_service.verify_grant(expired)
    assert claims is None
    assert error == ERR_GRANT_INVALID


async def test_grant_with_wrong_typ_is_invalid() -> None:
    """签名对但 typ 不对（例如拿别的内部 JWT 冒充）也必须拒。"""
    wrong_typ = jwt.encode(
        {
            'iss': GRANT_ISSUER,
            'aud': GRANT_AUDIENCE,
            'typ': 'something_else',
            'node_id': NODE_ID,
            'owner_hasn_id': OWNER,
            'user_id': USER_ID,
            'jti': 'wrong-typ-jti-0001',
            'exp': int(time.time()) + 60,
        },
        settings.TOKEN_SECRET_KEY,
        algorithm=GRANT_ALGORITHM,
    )
    claims, error = await access_ticket_service.verify_grant(wrong_typ)
    assert claims is None
    assert error == ERR_GRANT_INVALID


async def test_grant_invalid_is_403_not_401(monkeypatch) -> None:
    """grant 无效必须回 403，绝不能回 401。

    401 会被 daemon transport（`transports/huanxing.rs` 对 401 丢弃整个信封）归一成
    「凭据失效」，把「grant 坏 / 过期 / 重放」误报成「本容器设备凭据失效」，
    进而把运维指向错误的自救动作（去点「重新授权」）——2026-07-31 E2E 实测到的真 bug。
    口径：**401 专指调用方凭据问题；grant 不可接受一律 403。**
    """
    import inspect

    from backend.app.hasn_hosting.api.v1.node import cloud as cloud_api

    source = inspect.getsource(cloud_api.verify_session_grant)
    assert 'HTTP_401' not in source, 'grant 校验失败分支不得使用 401'
    assert source.count('ForbiddenError') >= 3, 'grant 无效/跨用户/跨节点三条拒绝都应是 403'
