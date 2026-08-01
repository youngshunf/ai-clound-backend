"""设备授权码一次性/过期/已用三分支 + node_type 不被覆盖（H2 · 契约 §3.2 / §0.2）——零 mock。

需本地 PostgreSQL :15432 与 Redis（兑换成功要真发 JWT session 进 Redis）。
"""

from __future__ import annotations

from datetime import timedelta
from typing import TYPE_CHECKING

import pytest
import pytest_asyncio

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from backend.app.hasn.model.hasn_nodes import HasnNodes
from backend.app.hasn.service.hasn_auth import register_node
from backend.app.hasn_hosting.constants import (
    CODE_PURPOSE_CREATE,
    CODE_STATUS_CONSUMED,
    ERR_CODE_CONSUMED,
    ERR_CODE_EXPIRED,
    ERR_CODE_NOT_FOUND,
)
from backend.app.hasn_hosting.model import HasnCloudNodes, HasnNodeAuthorizationCodes
from backend.app.hasn_hosting.service.authorization_code_service import authorization_code_service
from backend.app.hasn_hosting.service.node_credential_service import node_credential_service
from backend.common.security.jwt import revoke_token
from backend.core.conf import settings
from backend.database.db import SQLALCHEMY_DATABASE_URL, async_engine
from backend.database.redis import redis_client
from backend.utils.timezone import timezone

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

pytestmark = pytest.mark.asyncio

OWNER = 'h_hosting_code_owner'
USER_ID = 990101
NODE_ID = 'n_cloud_test_code_0001'
NODE_ID_OTHER = 'n_cloud_test_code_0002'


async def _purge(sess) -> None:
    await sess.execute(
        text("DELETE FROM hasn_node_authorization_codes WHERE owner_hasn_id = :o"), {'o': OWNER}
    )
    await sess.execute(text('DELETE FROM hasn_cloud_node_events WHERE node_id LIKE :p'), {'p': 'n_cloud_test_code_%'})
    await sess.execute(text('DELETE FROM hasn_cloud_nodes WHERE node_id LIKE :p'), {'p': 'n_cloud_test_code_%'})
    await sess.execute(text('DELETE FROM hasn_nodes WHERE node_id LIKE :p'), {'p': 'n_cloud_test_code_%'})
    await sess.commit()


def _cloud_node(node_id: str) -> HasnCloudNodes:
    return HasnCloudNodes(
        node_id=node_id,
        user_id=USER_ID,
        owner_hasn_id=OWNER,
        host='hosting-test',
        container_ref=None,
        status='provisioning',
        failure_reason=None,
        failure_detail=None,
        image_version='0.0.1',
        image_digest='sha256:' + '0' * 64,
        credential_session_uuid=None,
        retain_until=None,
        last_backup_at=None,
        online_since=None,
    )


@pytest_asyncio.fixture
async def sess() -> AsyncIterator:
    await async_engine.dispose()
    engine = create_async_engine(SQLALCHEMY_DATABASE_URL, poolclass=NullPool)
    try:
        async with engine.connect() as conn:
            await conn.execute(select(1))
    except Exception as exc:  # noqa: BLE001
        await engine.dispose()
        pytest.skip(f'本地 PostgreSQL 不可达，跳过: {exc!r}')
    s = async_sessionmaker(engine, expire_on_commit=False)()
    try:
        await _purge(s)
        s.add(_cloud_node(NODE_ID))
        s.add(_cloud_node(NODE_ID_OTHER))
        await s.commit()
        yield s
    finally:
        await _purge(s)
        await s.rollback()
        await s.close()
        await engine.dispose()
        await async_engine.dispose()


async def _mint(sess, node_id: str = NODE_ID) -> str:
    minted = await authorization_code_service.mint(
        sess, user_id=USER_ID, owner_hasn_id=OWNER, node_id=node_id, purpose=CODE_PURPOSE_CREATE
    )
    await sess.commit()
    return minted.plain_code


# ── 三分支 ──


async def test_consume_success_then_second_attempt_is_code_consumed(sess) -> None:
    """同一把码只能兑换一次；第二次必须精确落 code_consumed（不是 not_found）。"""
    code = await _mint(sess)

    row, failure = await authorization_code_service.consume(sess, plain_code=code, node_id=NODE_ID)
    await sess.commit()
    assert failure is None
    assert row is not None
    assert row.status == CODE_STATUS_CONSUMED
    assert row.consumed_at is not None

    row2, failure2 = await authorization_code_service.consume(sess, plain_code=code, node_id=NODE_ID)
    await sess.commit()
    assert row2 is None
    assert failure2 is not None and failure2.error == ERR_CODE_CONSUMED


async def test_expired_code_is_code_expired(sess) -> None:
    """过期码必须落 code_expired——原子 UPDATE 的 `expires_at>now()` 条件生效。"""
    code = await _mint(sess)
    await sess.execute(
        text('UPDATE hasn_node_authorization_codes SET expires_at = :t WHERE node_id = :n'),
        {'t': timezone.now() - timedelta(minutes=1), 'n': NODE_ID},
    )
    await sess.commit()

    row, failure = await authorization_code_service.consume(sess, plain_code=code, node_id=NODE_ID)
    await sess.commit()
    assert row is None
    assert failure is not None and failure.error == ERR_CODE_EXPIRED


async def test_unknown_code_is_code_not_found(sess) -> None:
    row, failure = await authorization_code_service.consume(
        sess, plain_code='definitely-not-a-real-code', node_id=NODE_ID
    )
    await sess.commit()
    assert row is None
    assert failure is not None and failure.error == ERR_CODE_NOT_FOUND


async def test_code_bound_to_other_node_is_code_not_found(sess) -> None:
    """码存在但绑的是别的节点 → 一律当不存在，不泄露归属。"""
    code = await _mint(sess, node_id=NODE_ID_OTHER)
    row, failure = await authorization_code_service.consume(sess, plain_code=code, node_id=NODE_ID)
    await sess.commit()
    assert row is None
    assert failure is not None and failure.error == ERR_CODE_NOT_FOUND


async def test_mint_revokes_previous_pending_code(sess) -> None:
    """重铸新码会作废旧的 pending 码：同一时刻只有一把有效钥匙。"""
    old_code = await _mint(sess)
    await _mint(sess)

    row, failure = await authorization_code_service.consume(sess, plain_code=old_code, node_id=NODE_ID)
    await sess.commit()
    assert row is None
    assert failure is not None and failure.error == ERR_CODE_EXPIRED


# ── 兑换端到端（真发 JWT session 进 Redis） ──


async def test_exchange_issues_device_credential_and_records_session(sess) -> None:
    code = await _mint(sess)
    credential, failure = await node_credential_service.exchange(
        sess, code=code, node_id=NODE_ID, client_ip='127.0.0.99'
    )
    await sess.commit()
    assert failure is None
    assert credential is not None
    assert credential.user_id == USER_ID
    assert credential.owner_hasn_id == OWNER
    assert credential.access_token and credential.refresh_token

    try:
        # session 真的落进 Redis（凭据可被单独吊销的前提）
        stored = await redis_client.get(
            f'{settings.TOKEN_REDIS_PREFIX}:{USER_ID}:{credential.session_uuid}'
        )
        assert stored == credential.access_token

        cloud_node = (
            await sess.execute(select(HasnCloudNodes).where(HasnCloudNodes.node_id == NODE_ID))
        ).scalar_one()
        await sess.refresh(cloud_node)
        assert cloud_node.credential_session_uuid == credential.session_uuid
        assert cloud_node.status == 'starting'
    finally:
        await revoke_token(USER_ID, credential.session_uuid)
        await redis_client.delete(
            f'{settings.TOKEN_REFRESH_REDIS_PREFIX}:{USER_ID}:{credential.session_uuid}'
        )


async def test_code_hash_only_plaintext_never_persisted(sess) -> None:
    """库里只留 sha256，明文码不得以任何形式落盘。"""
    code = await _mint(sess)
    rows = (
        await sess.execute(
            select(HasnNodeAuthorizationCodes).where(HasnNodeAuthorizationCodes.node_id == NODE_ID)
        )
    ).scalars().all()
    assert rows
    for row in rows:
        assert row.code_hash != code
        assert len(row.code_hash) == 64


# ── node_type 权威在云端（契约 §0.2） ──


async def test_register_node_does_not_overwrite_existing_cloud_node_type(sess) -> None:
    """容器上线走 WS Bearer 分支时会传 node_type='desktop'；已有行必须保留 'cloud'。"""
    sess.add(
        HasnNodes(
            node_id=NODE_ID,
            user_id=USER_ID,
            allowed_owner_hasn_ids=[OWNER],
            node_type='cloud',
            node_name='云端节点',
            device_fingerprint=None,
            device_platform='server',
            app_version=None,
            ip_address=None,
            ip_location=None,
            node_info={},
            node_key_hash=None,
            capacity=1,
            created_by_owner_id=OWNER,
            last_seen_at=None,
            status='active',
        )
    )
    await sess.commit()

    node = await register_node(
        db=sess, node_id=NODE_ID, user_id=USER_ID, owner_hasn_id=OWNER, node_type='desktop'
    )
    await sess.commit()
    assert node.node_type == 'cloud'

    reloaded = (await sess.execute(select(HasnNodes).where(HasnNodes.node_id == NODE_ID))).scalar_one()
    await sess.refresh(reloaded)
    assert reloaded.node_type == 'cloud'


async def test_register_node_new_row_still_defaults_to_desktop(sess) -> None:
    """不覆盖既有值不等于不生效：新建行仍按传入 node_type 落（桌面端行为不变）。"""
    node = await register_node(
        db=sess, node_id=NODE_ID_OTHER, user_id=USER_ID, owner_hasn_id=OWNER, node_type='desktop'
    )
    await sess.commit()
    assert node.node_type == 'desktop'
