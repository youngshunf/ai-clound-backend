"""doc94B M3：履约成功 → billing 失效推送链路验收（真实 PostgreSQL）。

94B §5 原本假定「A 卷 C2 已在 event 成功后从 Cloud 发出 WSPUSH 失效通知」。**实测不成立**，
而且断在两处，任何一处没修，真机买完积分账单中心都不会自动刷新：

1. **没人推**：唯一 bump `billing` kind 的地方是每日到期 sweeper，履约成功回调完全没推；
2. **推了也会被吞**：`compute_owner_billing_revision` 的指纹只由订阅行与权益行构成，
   而 doc94 之后钱包余额搬去了 NewAPI——**买积分包不改这两者中的任何一行**，指纹纹丝不动。
   daemon 的 `invalidate_resource` 按 `(owner, resource, revision)` 去重，
   revision 没变就返回 `Ok(false)` 当重复推送丢掉。

所以这里锁两件事：指纹**必须**随成功的履约事件变化；`_notify_billing_changed` 在没有 owner
身份或推送异常时**不能**把已经成功的履约拖垮（权威数据已落地，推送只是让镜像早点回填）。
"""

from __future__ import annotations

import uuid

import pytest

from sqlalchemy import text

from backend.app.billing.service.credit_outbox_service import CreditOutboxService
from backend.app.hasn.service.sync_invalidate_service import compute_owner_billing_revision
from backend.database.db import async_db_session

pytestmark = pytest.mark.asyncio

_APP_CODE = 'doc94b'


async def _reset_engine() -> None:
    """丢弃跨事件循环的连接池（asyncpg 连接绑定在创建它的 loop 上）。"""
    from backend.database.db import async_engine

    await async_engine.dispose()


async def _seed_owner(user_id: int, owner_hasn: str) -> None:
    async with async_db_session.begin() as db:
        await db.execute(text('DELETE FROM hasn_humans WHERE hasn_id = :h'), {'h': owner_hasn})
        await db.execute(
            text("""
                INSERT INTO hasn_humans (hasn_id, star_id, user_id, nickname, status, created_time)
                VALUES (:h, :s, :u, 'doc94b', 'active', NOW())
            """),
            {'h': owner_hasn, 's': f's{user_id}', 'u': user_id},
        )


async def _add_succeeded_grant(user_id: int, credits: str) -> None:
    async with async_db_session.begin() as db:
        await db.execute(
            text("""
                INSERT INTO hasn_billing.credit_grant_event
                    (event_id, idempotency_key, event_type, app_code, user_id, newapi_user_id,
                     credit_amount, applied_credits, payload, payload_hash, status,
                     attempt_count, delivered_at, created_time)
                VALUES (:eid, :key, 'wallet_grant', :app, :u, :u,
                        :credits, :credits, '{}'::jsonb, 'h', 'succeeded',
                        1, NOW(), NOW())
            """),
            {
                'eid': str(uuid.uuid4()),
                'key': f'admin:DOC94B-{uuid.uuid4().hex[:12]}:wallet-grant',
                'app': _APP_CODE,
                'u': user_id,
                'credits': credits,
            },
        )


async def _cleanup(user_id: int, owner_hasn: str) -> None:
    async with async_db_session.begin() as db:
        await db.execute(text('DELETE FROM hasn_billing.credit_grant_event WHERE user_id = :u'), {'u': user_id})
        await db.execute(text('DELETE FROM hasn_humans WHERE hasn_id = :h'), {'h': owner_hasn})


async def test_billing_revision_moves_when_credits_land() -> None:
    """积分到账必须改变 billing 指纹。

    这是整条链路的命门：指纹不变 → daemon 按重复推送丢弃 → 用户买完积分看不到到账。
    补这一条之前，订阅行与权益行都没动，指纹前后完全相同。
    """
    await _reset_engine()
    user_id = 978_100_001
    owner_hasn = 'h_doc94b_rev_1'
    await _seed_owner(user_id, owner_hasn)
    try:
        async with async_db_session() as db:
            before = await compute_owner_billing_revision(db, owner_hasn)

        await _add_succeeded_grant(user_id, '10')

        async with async_db_session() as db:
            after_first = await compute_owner_billing_revision(db, owner_hasn)

        assert after_first != before, '第一笔积分到账后指纹必须变，否则失效推送会被当重复丢掉'

        # 第二笔同样金额：计数与最新 id 都变，指纹必须再次变化
        await _add_succeeded_grant(user_id, '10')
        async with async_db_session() as db:
            after_second = await compute_owner_billing_revision(db, owner_hasn)

        assert after_second != after_first, '同额的第二笔也必须让指纹变，否则连续两笔只会刷新一次'
    finally:
        await _cleanup(user_id, owner_hasn)


async def test_billing_revision_is_stable_without_new_grants() -> None:
    """没有新到账时指纹必须稳定——否则每次读都触发一次无谓的全量回源。"""
    await _reset_engine()
    user_id = 978_100_002
    owner_hasn = 'h_doc94b_rev_2'
    await _seed_owner(user_id, owner_hasn)
    try:
        await _add_succeeded_grant(user_id, '5')
        async with async_db_session() as db:
            first = await compute_owner_billing_revision(db, owner_hasn)
            second = await compute_owner_billing_revision(db, owner_hasn)
        assert first == second, '无变更时指纹必须稳定'
    finally:
        await _cleanup(user_id, owner_hasn)


async def test_pending_grants_do_not_move_revision() -> None:
    """未到账的命令不该让指纹变。

    `pending` 表示钱还没发出去。让它触发刷新，用户会在余额没变的情况下看到页面闪一下，
    然后什么都没发生——那是噪音，不是信号。
    """
    await _reset_engine()
    user_id = 978_100_003
    owner_hasn = 'h_doc94b_rev_3'
    await _seed_owner(user_id, owner_hasn)
    try:
        async with async_db_session() as db:
            before = await compute_owner_billing_revision(db, owner_hasn)

        async with async_db_session.begin() as db:
            await db.execute(
                text("""
                    INSERT INTO hasn_billing.credit_grant_event
                        (event_id, idempotency_key, event_type, app_code, user_id, newapi_user_id,
                         credit_amount, payload, payload_hash, status, attempt_count, created_time)
                    VALUES (:eid, :key, 'wallet_grant', :app, :u, :u,
                            7, '{}'::jsonb, 'h', 'pending', 0, NOW())
                """),
                {
                    'eid': str(uuid.uuid4()),
                    'key': f'admin:DOC94B-P-{uuid.uuid4().hex[:12]}:wallet-grant',
                    'app': _APP_CODE,
                    'u': user_id,
                },
            )

        async with async_db_session() as db:
            after = await compute_owner_billing_revision(db, owner_hasn)

        assert after == before, '未到账的 pending 命令不该触发刷新'
    finally:
        await _cleanup(user_id, owner_hasn)


async def test_notify_is_best_effort_and_never_breaks_a_landed_fulfillment() -> None:
    """推送失败不得拖垮已经成功的履约。

    额度已经在 NewAPI 落地、云端履约状态也已提交，此时推送只是让镜像早点回填。
    这里让 bump 直接抛，断言 `_notify_billing_changed` 吞掉异常正常返回。
    """
    await _reset_engine()
    user_id = 978_100_004
    owner_hasn = 'h_doc94b_rev_4'
    await _seed_owner(user_id, owner_hasn)
    try:
        await _add_succeeded_grant(user_id, '3')
        async with async_db_session() as db:
            event_id = (
                await db.execute(
                    text('SELECT event_id FROM hasn_billing.credit_grant_event WHERE user_id = :u LIMIT 1'),
                    {'u': user_id},
                )
            ).scalar()

        import backend.app.hasn.service.sync_invalidate_service as invalidate_module

        original = invalidate_module.bump_owner

        async def _boom(*args, **kwargs):
            raise RuntimeError('模拟推送通道故障')

        invalidate_module.bump_owner = _boom  # type: ignore[assignment]
        try:
            # 不抛即通过：履约已成功，推送失败可自愈
            await CreditOutboxService._notify_billing_changed(str(event_id))
        finally:
            invalidate_module.bump_owner = original  # type: ignore[assignment]
    finally:
        await _cleanup(user_id, owner_hasn)


async def test_notify_skips_users_without_owner_identity() -> None:
    """没有 owner 身份的用户（admin 造数等）不推送，且不算失败。"""
    await _reset_engine()
    user_id = 978_100_005
    owner_hasn = 'h_doc94b_rev_5_absent'
    try:
        await _add_succeeded_grant(user_id, '1')
        async with async_db_session() as db:
            event_id = (
                await db.execute(
                    text('SELECT event_id FROM hasn_billing.credit_grant_event WHERE user_id = :u LIMIT 1'),
                    {'u': user_id},
                )
            ).scalar()
        # 该 user_id 没有 hasn_humans 行 → 直接返回，不抛
        await CreditOutboxService._notify_billing_changed(str(event_id))
    finally:
        await _cleanup(user_id, owner_hasn)
