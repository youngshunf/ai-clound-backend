"""积分账本闭环 §5A 集成测试（零 mock，真实 new-api :3180 + 真实 PG）。

验证 D6/§5A.4/§5A.5 全链路：
- sync_quota_to_balance：入账后 new-api 可用额度 = 账本剩余×RATE（积分包/订阅入账修复）。
- reconcile_user：增量回扣账本 → 重设 quota → 推进游标（每小时同步）。
- no-delta 幂等：无新消费时不重复扣减。
- 封顶：消费超过可用余额时回扣封顶在可用余额，quota 归 0。

new-api 真实写 quota、本地 PG 真实记账（user_credit_balance/credit_transaction）——零 fake。
唯一被「注入」的是游标 last_synced_used_quota（本服务自有的记账字段），用于模型化
「上次同步以来的消费」（驱动真实 used_quota 增长需一次活体 LLM relay 调用，昂贵且不稳定，
故以游标偏移建模该唯一输入；new-api quota 写入与账本扣减全部真实发生）。

new-api 不可达或 PG 连不上时整体 skip（infra-gated）；可达则全程真实联调并自清理。
"""

from __future__ import annotations

import secrets

from decimal import Decimal

import httpx
import pytest

from sqlalchemy import delete

from backend.app.billing.model import CreditTransaction, UserCreditBalance, UserSubscription
from backend.app.billing.service.credit_service import credit_service
from backend.app.newapi.credit_sync_service import credit_sync_service
from backend.app.newapi.crud import llm_newapi_user_mapping_dao
from backend.app.newapi.model.llm_newapi_user_mapping import LlmNewapiUserMapping
from backend.app.newapi.service import llm_newapi_user_mapping_service as svc
from backend.core.conf import settings
from backend.database.db import async_db_session

pytestmark = pytest.mark.asyncio

RATE = settings.NEWAPI_QUOTA_PER_DOLLAR


def _newapi_reachable() -> bool:
    if not settings.NEWAPI_ADMIN_ACCESS_TOKEN:
        return False
    try:
        with httpx.Client(timeout=3, trust_env=False) as c:
            r = c.get(f'{settings.NEWAPI_ADMIN_BASE_URL.rstrip("/")}/status')
            return r.status_code == 200 and r.json().get('success') is True
    except Exception:
        return False


pytest_skip = pytest.mark.skipif(
    not _newapi_reachable(),
    reason='new-api :3180 不可达或未配 NEWAPI_ADMIN_ACCESS_TOKEN（集成测试需真实 new-api，零 mock）',
)


@pytest_skip
async def test_credit_ledger_closed_loop() -> None:
    from backend.app.newapi.client import newapi_admin_client

    huanxing_user_id = 910_000_000 + secrets.randbelow(80_000_000)
    newapi_user_id: int | None = None
    try:
        # ── A. 建用户 + 充值 100 积分 + 入账推额度（§5A.4 / D6 / 积分包修复）──
        # 注：ensure 触发 get_or_create_subscription 会按 free 档自动赠 100 月度积分，
        # 故起始余额 = 100(free) + 100(购买) = 200；用相对断言不写死，验证 D6 镜像本身。
        async with async_db_session.begin() as db:
            info = await svc.ensure_newapi_user(db, huanxing_user_id)
            newapi_user_id = info.newapi_user_id
            await credit_service.add_credits(
                db,
                user_id=huanxing_user_id,
                credits=Decimal(100),
                transaction_type='purchase',
                description='测试充值 100 积分',
                is_purchased=True,
                expires_at=None,
            )
            ok = await credit_sync_service.sync_quota_to_balance(db, huanxing_user_id)
            assert ok is True

        async with async_db_session() as db:
            start_remaining = await credit_service.get_total_available_credits(db, huanxing_user_id)
        assert start_remaining >= Decimal(100), '充值 100 后余额至少含购买的 100'
        q = await newapi_admin_client.get_user_quota(newapi_user_id)
        assert q['used_quota'] == 0
        assert q['quota'] == int(start_remaining * RATE), 'D6：new-api 可用 = 账本剩余 × RATE（镜像）'

        # ── B. 模型化「消费 30 积分」（游标下移）→ reconcile：回扣 + 重设 quota + 推进游标 ──
        async with async_db_session.begin() as db:
            m = await llm_newapi_user_mapping_dao.get_by_user(db, huanxing_user_id, 'huanxing')
            m.last_synced_used_quota = q['used_quota'] - 30 * RATE  # 模型化已消费 30 积分

        async with async_db_session.begin() as db:
            res = await credit_sync_service.reconcile_user(db, huanxing_user_id)
            assert res['status'] == 'ok'
            assert res['consumed_credits'] == Decimal(30)

        async with async_db_session() as db:
            remaining = await credit_service.get_total_available_credits(db, huanxing_user_id)
            assert remaining == start_remaining - Decimal(30), '回扣 30 后账本应减 30'
            m = await llm_newapi_user_mapping_dao.get_by_user(db, huanxing_user_id, 'huanxing')
            assert m.last_synced_used_quota == q['used_quota'], '游标应推进到当前 used_quota'
        q2 = await newapi_admin_client.get_user_quota(newapi_user_id)
        assert q2['quota'] == int((start_remaining - 30) * RATE), 'reconcile 后 new-api 可用 = 剩余 × RATE'

        # ── C. 无新消费再次 reconcile：幂等，不重复扣减 ──
        async with async_db_session.begin() as db:
            res = await credit_sync_service.reconcile_user(db, huanxing_user_id)
            assert res['status'] == 'no_delta'
        async with async_db_session() as db:
            remaining = await credit_service.get_total_available_credits(db, huanxing_user_id)
            assert remaining == start_remaining - Decimal(30), '无新消费余额不变'

        # ── D. 消费量超过可用余额：回扣封顶在可用余额，quota 归 0（零 fake，不臆造负余额）──
        over = start_remaining + Decimal(100)  # 模型化消费超过可用
        async with async_db_session.begin() as db:
            m = await llm_newapi_user_mapping_dao.get_by_user(db, huanxing_user_id, 'huanxing')
            m.last_synced_used_quota = q['used_quota'] - int(over) * RATE

        async with async_db_session.begin() as db:
            res = await credit_sync_service.reconcile_user(db, huanxing_user_id)
            assert res['status'] == 'ok'
            assert res['consumed_credits'] == over  # 报告真实增量（不封顶报告值）
        async with async_db_session() as db:
            remaining = await credit_service.get_total_available_credits(db, huanxing_user_id)
            assert remaining == Decimal(0), '回扣封顶在可用余额，余额清零（不臆造负数）'
        q3 = await newapi_admin_client.get_user_quota(newapi_user_id)
        assert q3['quota'] == 0, '余额清零后 new-api 可用 = 0'
    finally:
        # 自清理：删 new-api 用户 + 本地映射 + 账本/流水/订阅
        if newapi_user_id is not None:
            await newapi_admin_client.delete_user(newapi_user_id)
        async with async_db_session.begin() as db:
            await db.execute(
                delete(LlmNewapiUserMapping).where(
                    LlmNewapiUserMapping.huanxing_user_id == huanxing_user_id
                )
            )
            await db.execute(delete(UserCreditBalance).where(UserCreditBalance.user_id == huanxing_user_id))
            await db.execute(delete(CreditTransaction).where(CreditTransaction.user_id == huanxing_user_id))
            await db.execute(delete(UserSubscription).where(UserSubscription.user_id == huanxing_user_id))
