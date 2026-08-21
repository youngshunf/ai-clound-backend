"""非支付路径的额度授予（doc94 F1）：免费档、注册奖励、管理员赠送/撤销。

三条路径过去都直接改云端余额，是「双权威」的另外三个入口。现在统一收敛成
「写一条履约命令 → outbox 投递给 NewAPI」，与支付履约走同一套幂等与审计。

**幂等键的三处要害**（写错任何一处，结果都是「该发的发不出去」或「不该发的发两次」）：

- 免费档键必须带 ``policy_version`` 与 ``epoch``。免费政策允许「撤销后重新授予」，
  若键只是 ``free:{user_id}:activate``，撤销后再授予会被自己写下的键永久挡住——
  该用户此生再也发不出第二次免费额度。
- 管理员赠送/撤销以**单据号**为键，不以 ``user_id`` 为键：同一个用户可以有多笔独立赠送，
  用 ``user_id`` 会把第二笔当成重放吞掉。
- 活动奖励带 ``campaign_version``：同一活动改额度后可以重新发放，
  而同一活动同一版本对同一用户永远只发一次。
"""

from __future__ import annotations

import uuid

from decimal import Decimal
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.billing.model.credit_grant_event import CreditGrantEvent
from backend.app.billing.model.user_subscription import UserSubscription
from backend.app.billing.service import offering_pricing
from backend.app.billing.service.contract_status import CURRENT_CONTRACT_STATUSES
from backend.app.billing.service.credit_grant_event_service import (
    CYCLE_SECONDS,
    EVENT_SUBSCRIPTION_ACTIVATE,
    EVENT_SUBSCRIPTION_EXPIRE,
    EVENT_WALLET_GRANT,
    EVENT_WALLET_REVOKE,
    IdempotencyKeys,
    credit_grant_event_service,
    format_credits,
)
from backend.common.exception import errors
from backend.common.log import log
from backend.core.conf import settings
from backend.utils.timezone import timezone


def _rfc3339(value: Any) -> str:
    return value.astimezone().isoformat()


class CreditGrantService:
    """免费档 / 注册奖励 / 管理员赠送的命令登记。"""

    @staticmethod
    async def _newapi_user_id(db: AsyncSession, user_id: int, app_code: str) -> int:
        from backend.app.billing.service.pay_callbacks import _resolve_newapi_user_id

        return await _resolve_newapi_user_id(db, user_id, app_code)

    # ── 免费档 ────────────────────────────────────────────────────────────

    @staticmethod
    async def ensure_free_contract(
        db: AsyncSession, *, user_id: int, app_code: str = 'huanxing'
    ) -> UserSubscription | None:
        """确保用户有一份生效中的免费合同；已有生效合同则原样返回。

        免费合同的字段口径（三处必须一致）：
        ``cycle_seconds`` 恒为 30 天（它定义「多久清零重置一次」）、
        ``cycle_count`` 为空（无限期循环，没有第 N 期后的合同终点）、
        ``contract_end_at`` 为空（无商业到期）。绝不用「100 年到期」伪造永久合同。
        """
        existing = (
            await db.execute(
                select(UserSubscription).where(
                    UserSubscription.app_code == app_code,
                    UserSubscription.user_id == user_id,
                    # 取消自动续费的合同仍然生效，必须算进来，否则会重复建免费合同。
                    UserSubscription.status.in_(CURRENT_CONTRACT_STATUSES),
                )
            )
        ).scalar_one_or_none()
        if existing is not None:
            # 已有合同**不等于**已经履约。doc94 之前建的免费合同没有
            # `external_subscription_id`，也从未进过 outbox；而这里过去无条件
            # `return existing`，于是那批合同此生都拿不到 NewAPI 侧的订阅池——
            # 合同上写着「每 30 天 100 积分」，权威侧一个订阅都没有。
            await CreditGrantService.backfill_free_contract_fulfillment(db, existing, app_code=app_code)
            return existing

        # doc94 D1：档位配置的唯一事实源是商品目录 billing_plan，不再读 subscription_tier。
        tier = await offering_pricing.get_tier(db, 'free')
        if tier is None:
            log.warning(f'[CreditGrant] 免费档配置缺失，跳过免费合同创建: app_code={app_code}')
            return None
        credits_per_cycle = tier.credits_per_cycle
        if credits_per_cycle <= 0:
            log.warning(f'[CreditGrant] 免费档未配置每周期额度，跳过: app_code={app_code}')
            return None
        storage_bytes = tier.storage_bytes
        if storage_bytes <= 0:
            log.warning(f'[CreditGrant] 免费档未配置存储额度，跳过: app_code={app_code}')
            return None

        # epoch 取该用户历史最大值 +1：每次「失效 → 重新授予」都必须换一个新键。
        previous_epoch = (
            await db.execute(
                select(UserSubscription.free_grant_epoch)
                .where(
                    UserSubscription.app_code == app_code,
                    UserSubscription.user_id == user_id,
                    UserSubscription.tier == 'free',
                )
                .order_by(UserSubscription.free_grant_epoch.desc())
                .limit(1)
            )
        ).scalar()
        epoch = int(previous_epoch or 0) + 1
        policy_version = int(settings.FREE_TIER_POLICY_VERSION)

        now = timezone.now()
        contract_no = f'HXF{uuid.uuid4().hex[:20].upper()}'
        contract = UserSubscription(
            app_code=app_code,
            user_id=user_id,
            tier='free',
            subscription_type='free',
            monthly_credits=Decimal(0),
            current_credits=Decimal(0),
            used_credits=Decimal(0),
            purchased_credits=Decimal(0),
            billing_cycle_start=now,
            billing_cycle_end=now,
            subscription_start_date=now,
            subscription_end_date=None,
            status='active',
            auto_renew=False,
            max_agents=tier.max_agents,
            contract_no=contract_no,
            contract_start_at=now,
            contract_end_at=None,
            cycle_seconds=CYCLE_SECONDS,
            cycle_count=None,
            plan_snapshot={
                'tier': 'free',
                'credits_per_cycle': format_credits(credits_per_cycle),
                'storage_bytes': storage_bytes,
                'cycle_seconds': CYCLE_SECONDS,
                'cycle_count': None,
            },
            external_subscription_id=contract_no,
            fulfillment_status='pending',
            free_policy_version=policy_version,
            free_grant_epoch=epoch,
        )
        db.add(contract)
        await db.flush()

        await credit_grant_event_service.enqueue(
            db,
            event_type=EVENT_SUBSCRIPTION_ACTIVATE,
            idempotency_key=IdempotencyKeys.free_activate(user_id, policy_version, epoch),
            user_id=user_id,
            newapi_user_id=await CreditGrantService._newapi_user_id(db, user_id, app_code),
            app_code=app_code,
            credit_amount=credits_per_cycle,
            subscription_id=contract.id,
            contract_no=contract_no,
            payload_extra={
                'external_subscription_id': contract_no,
                'start_at': _rfc3339(now),
                'end_at': None,
                'cycle_seconds': CYCLE_SECONDS,
                'cycle_count': None,
                'wallet_overflow': True,
                'reason': 'free_tier',
            },
        )
        log.info(f'[CreditGrant] 免费合同已建立: user_id={user_id} contract_no={contract_no} epoch={epoch}')
        return contract

    @staticmethod
    async def backfill_free_contract_fulfillment(
        db: AsyncSession, contract: UserSubscription, *, app_code: str
    ) -> bool:
        """给「doc94 之前建的、从未履约过」的免费合同补一次履约登记。返回是否补了。

        判据只有一个：**合同没有 `external_subscription_id`**。那是合同与 NewAPI 侧
        订阅池之间唯一的连接键，它为空就说明这份合同从未被投递过——不能靠
        `fulfillment_status` 判，那一列是 2026-07-25 加列时给存量行落的 DEFAULT
        `'not_required'`，含义是「加列那天没人回填」，不是「确认无需履约」。

        幂等由 `IdempotencyKeys.free_activate(user_id, policy_version, epoch)` 保证，
        与正常建合同走同一个键空间：重复调用只会撞上同一个键，不会发第二份额度。
        """
        if contract.tier != 'free':
            return False
        if (contract.external_subscription_id or '').strip():
            return False

        tier = await offering_pricing.get_tier(db, 'free')
        if tier is None or tier.credits_per_cycle <= 0:
            log.warning(f'[CreditGrant] 免费档配置缺失，跳过存量合同回填: user_id={contract.user_id}')
            return False

        # **先解析映射再改合同**：解析不到时必须一个字段都没动过就退出。
        # 反过来（先写字段、enqueue 失败再吞掉异常）会留下一份「已标 pending、
        # 有 external_subscription_id、但 outbox 里没有对应命令」的合同——
        # 它既不会被投递，也不会再被本方法认领（判据正是那个键为空），永久卡死。
        from backend.app.newapi.crud import llm_newapi_user_mapping_dao

        mapping = await llm_newapi_user_mapping_dao.get_by_user(db, contract.user_id, app_code)
        if not mapping or not mapping.newapi_user_id:
            # 尚未开通 NewAPI 账户：这是「还不能履约」，不是「不该履约」。
            # 保持原状，等 sweep 下一轮或开户完成后再来。
            log.warning(f'[CreditGrant] 尚无 NewAPI 映射，暂不回填存量免费合同: user_id={contract.user_id}')
            return False

        now = timezone.now()
        contract_no = (contract.contract_no or '').strip() or f'HXF{uuid.uuid4().hex[:20].upper()}'
        policy_version = int(contract.free_policy_version or settings.FREE_TIER_POLICY_VERSION)
        epoch = int(contract.free_grant_epoch or 1)

        contract.contract_no = contract_no
        contract.external_subscription_id = contract_no
        contract.free_policy_version = policy_version
        contract.free_grant_epoch = epoch
        contract.fulfillment_status = 'pending'
        # 存量行的 cycle_seconds 可能为空；周期长度是履约命令的必填项，NewAPI 侧
        # 会校验它必须恰好等于 CycleSecondsFixed。
        if not contract.cycle_seconds:
            contract.cycle_seconds = CYCLE_SECONDS
        if contract.contract_start_at is None:
            contract.contract_start_at = contract.subscription_start_date or now
        contract.monthly_credits = tier.credits_per_cycle

        await credit_grant_event_service.enqueue(
            db,
            event_type=EVENT_SUBSCRIPTION_ACTIVATE,
            idempotency_key=IdempotencyKeys.free_activate(contract.user_id, policy_version, epoch),
            user_id=contract.user_id,
            newapi_user_id=int(mapping.newapi_user_id),
            app_code=app_code,
            credit_amount=tier.credits_per_cycle,
            subscription_id=contract.id,
            contract_no=contract_no,
            payload_extra={
                'external_subscription_id': contract_no,
                # 起点用合同锚点而不是「此刻」：周期边界应当延续这份合同本来的节奏，
                # 用回填当天当起点会让所有存量用户的重置日挤在同一天。
                'start_at': _rfc3339(contract.contract_start_at or now),
                'end_at': None,
                'cycle_seconds': int(contract.cycle_seconds or CYCLE_SECONDS),
                'cycle_count': None,
                'wallet_overflow': True,
                'reason': 'free_tier_backfill',
            },
        )
        log.info(
            f'[CreditGrant] 存量免费合同补履约登记: user_id={contract.user_id} '
            f'contract_no={contract_no} policy_version={policy_version} epoch={epoch}'
        )
        return True

    @staticmethod
    async def revoke_free_contract(db: AsyncSession, *, user_id: int, app_code: str = 'huanxing') -> None:
        """撤销免费额度（封禁 / 注销 / 免费政策撤销）：终止合同并登记到期命令。

        撤销后再授予会拿到 ``epoch + 1`` 的新幂等键，因此**不会**被本次写下的键挡住。
        """
        contract = (
            await db.execute(
                select(UserSubscription)
                .where(
                    UserSubscription.app_code == app_code,
                    UserSubscription.user_id == user_id,
                    UserSubscription.tier == 'free',
                    UserSubscription.status == 'active',
                )
                .with_for_update()
            )
        ).scalar_one_or_none()
        if contract is None or not contract.external_subscription_id:
            return
        contract.status = 'expired'
        contract.contract_end_at = timezone.now()
        contract.fulfillment_status = 'pending'
        await credit_grant_event_service.enqueue(
            db,
            event_type=EVENT_SUBSCRIPTION_EXPIRE,
            idempotency_key=IdempotencyKeys.subscription_expire(contract.contract_no or f'free-{contract.id}'),
            user_id=user_id,
            newapi_user_id=await CreditGrantService._newapi_user_id(db, user_id, app_code),
            app_code=app_code,
            subscription_id=contract.id,
            contract_no=contract.contract_no,
            payload_extra={'external_subscription_id': contract.external_subscription_id, 'reason': 'free_revoked'},
        )
        log.info(f'[CreditGrant] 免费合同已撤销: user_id={user_id} contract_no={contract.contract_no}')

    # ── 注册奖励 ──────────────────────────────────────────────────────────

    @staticmethod
    async def grant_registration_bonus(
        db: AsyncSession, *, user_id: int, app_code: str = 'huanxing'
    ) -> CreditGrantEvent | None:
        """新用户注册赠送：登记一条永久钱包发放命令。

        幂等键含 ``campaign_version``：调整赠送额度时递增版本即可重新发放，
        同版本对同一用户永远只发一次。
        """
        credits = Decimal(str(settings.NEWAPI_REGISTER_BONUS_CREDITS or 0))
        if credits <= 0:
            return None
        return await credit_grant_event_service.enqueue(
            db,
            event_type=EVENT_WALLET_GRANT,
            idempotency_key=IdempotencyKeys.campaign_bonus(
                settings.REGISTER_BONUS_CAMPAIGN_KEY, int(settings.REGISTER_BONUS_CAMPAIGN_VERSION), user_id
            ),
            user_id=user_id,
            newapi_user_id=await CreditGrantService._newapi_user_id(db, user_id, app_code),
            app_code=app_code,
            credit_amount=credits,
            payload_extra={'reason': 'register_bonus'},
        )

    # ── 管理员赠送 / 撤销 ─────────────────────────────────────────────────

    @staticmethod
    async def admin_grant(
        db: AsyncSession,
        *,
        user_id: int,
        credits: Decimal,
        grant_no: str | None = None,
        app_code: str = 'huanxing',
        reason: str = 'admin_grant',
    ) -> CreditGrantEvent:
        """管理员赠送：以单据号为幂等键登记一条钱包发放命令。

        ``grant_no`` 每张单据独立，因此同一用户的连续两笔赠送都会真实到账，
        第二笔不会被当成第一笔的重放。
        """
        if credits <= 0:
            raise errors.RequestError(msg='赠送积分必须大于 0')
        grant_no = grant_no or f'AG{uuid.uuid4().hex[:18].upper()}'
        return await credit_grant_event_service.enqueue(
            db,
            event_type=EVENT_WALLET_GRANT,
            idempotency_key=IdempotencyKeys.admin_grant(grant_no),
            user_id=user_id,
            newapi_user_id=await CreditGrantService._newapi_user_id(db, user_id, app_code),
            app_code=app_code,
            credit_amount=credits,
            payload_extra={'reason': reason[:64], 'grant_no': grant_no},
        )

    @staticmethod
    async def admin_revoke(
        db: AsyncSession,
        *,
        user_id: int,
        credits: Decimal,
        revoke_no: str | None = None,
        app_code: str = 'huanxing',
        reason: str = 'admin_revoke',
    ) -> CreditGrantEvent:
        """管理员撤销：同样以单据号为键。余额不足由 NewAPI 终局拒绝，钱包永不为负。"""
        if credits <= 0:
            raise errors.RequestError(msg='撤销积分必须大于 0')
        revoke_no = revoke_no or f'AR{uuid.uuid4().hex[:18].upper()}'
        return await credit_grant_event_service.enqueue(
            db,
            event_type=EVENT_WALLET_REVOKE,
            idempotency_key=IdempotencyKeys.admin_revoke(revoke_no),
            user_id=user_id,
            newapi_user_id=await CreditGrantService._newapi_user_id(db, user_id, app_code),
            app_code=app_code,
            credit_amount=credits,
            payload_extra={'reason': reason[:64], 'revoke_no': revoke_no},
        )


credit_grant_service: CreditGrantService = CreditGrantService()
