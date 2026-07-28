"""订阅合同服务（doc94 C2/F2）：合同是商业事实，额度是 NewAPI 事实。

云端只管「买了什么、何时生效、何时到期」，NewAPI 管「还剩多少、什么时候清零」。
本服务负责在**支付回调的同一事务内**建立/终止合同，并登记对应的履约命令；
它从不计算余额，也从不写余额。

周期口径（全链路一致，绝不使用自然月）：

- 一个周期 = 30 天 = 2_592_000 秒；
- 月付 = 1 个周期（30 天），到期只清零不重置；
- 年付 = 12 个连续周期（360 天），每 30 天清零重置，第 360 天到期不再重置；
- 免费档 = 无商业到期（``contract_end_at`` 为空）+ ``cycle_count`` 为空（无限期循环），
  但 ``cycle_seconds`` 仍必填——它定义「多久清零重置一次」。
"""

from __future__ import annotations

import uuid

from datetime import timedelta
from decimal import Decimal
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.billing.model.user_subscription import UserSubscription
from backend.app.billing.service import offering_pricing
from backend.app.billing.service.contract_status import CURRENT_CONTRACT_STATUSES, STATUS_CANCEL_AT_PERIOD_END
from backend.app.billing.service.credit_grant_event_service import (
    CYCLE_SECONDS,
    EVENT_SUBSCRIPTION_ACTIVATE,
    EVENT_SUBSCRIPTION_EXPIRE,
    IdempotencyKeys,
    credit_grant_event_service,
    format_credits,
)
from backend.common.exception import errors
from backend.common.log import log
from backend.utils.timezone import timezone

#: 月付 1 期、年付 12 期。这是商品合同参数，不是余额。
CYCLE_COUNT_BY_BILLING_CYCLE = {'monthly': 1, 'yearly': 12}

# 合同状态常量统一在 contract_status 模块，避免两处判定漂移。


def _rfc3339(value: Any) -> str:
    return value.astimezone().isoformat()


class SubscriptionContractService:
    """订阅合同的建立、终止与履约命令登记。"""

    @staticmethod
    async def _current_active(db: AsyncSession, *, app_code: str, user_id: int) -> UserSubscription | None:
        result = await db.execute(
            select(UserSubscription)
            .where(
                UserSubscription.app_code == app_code,
                UserSubscription.user_id == user_id,
                UserSubscription.status.in_(CURRENT_CONTRACT_STATUSES),
            )
            .with_for_update()
        )
        return result.scalar_one_or_none()

    @staticmethod
    async def activate_from_order(db: AsyncSession, *, order: Any) -> UserSubscription:
        """支付成功后建立一份合同，并登记 ``subscription_activate`` 命令。

        三种形态：
        - 用户当前没有生效合同 → 新合同立即生效；
        - 有生效合同且换档（升级）→ 旧合同当场终止（登记到期命令，剩余额度由 NewAPI 清零），新合同立即生效；
        - 有生效合同且同档续费 → 新合同排在旧合同到期之后（``scheduled``），到点由 NewAPI 原子切换，
          期间不得提前消费。
        """
        app_code = (getattr(order, 'extra_data', None) or {}).get('app_code', 'huanxing')
        tier_name = getattr(order, 'target_tier', None)
        billing_cycle = getattr(order, 'billing_cycle', None) or 'monthly'
        if not tier_name:
            raise errors.RequestError(msg=f'订阅订单 {order.order_no} 缺少目标套餐')
        cycle_count = CYCLE_COUNT_BY_BILLING_CYCLE.get(billing_cycle)
        if not cycle_count:
            raise errors.RequestError(msg=f'不支持的计费周期: {billing_cycle}')

        # doc94 D1：档位配置的唯一事实源是商品目录 billing_plan，不再读 subscription_tier。
        tier = await offering_pricing.get_tier(db, tier_name)
        if tier is None:
            raise errors.RequestError(msg=f'套餐配置不存在或未启用: {tier_name}')
        credits_per_cycle = tier.credits_per_cycle
        if credits_per_cycle <= 0:
            raise errors.RequestError(msg=f'套餐 {tier_name} 未配置每周期积分额度，拒绝履约')
        if tier.storage_bytes <= 0:
            raise errors.RequestError(msg=f'套餐 {tier_name} 未配置存储空间额度，拒绝履约')

        from backend.app.billing.service.pay_callbacks import _resolve_newapi_user_id

        newapi_user_id = await _resolve_newapi_user_id(db, order.user_id, app_code)

        now = timezone.now()
        current = await SubscriptionContractService._current_active(db, app_code=app_code, user_id=order.user_id)

        start_at = now
        status = 'active'
        if current is not None:
            current_rank = await SubscriptionContractService._tier_rank(db, current.tier, app_code)
            target_rank = await SubscriptionContractService._tier_rank(db, tier_name, app_code)
            if current.tier == tier_name:
                # 同档续费：新合同排在旧合同之后，不得提前消费。
                start_at = current.contract_end_at or now
                if start_at <= now:
                    start_at = now
                else:
                    status = 'scheduled'
            elif target_rank > current_rank:
                # 升级（首版规则）：全价购买目标档，新合同立即生效；
                # 旧订阅剩余额度由 NewAPI 清零且不退款——不做按比例折算，
                # 折算需要把「已用多少」当成云端事实，而用量权威在 NewAPI。
                await SubscriptionContractService._terminate(
                    db,
                    contract=current,
                    idempotency_key=IdempotencyKeys.subscription_expire(current.contract_no or f'legacy-{current.id}'),
                    reason='upgrade_supersede',
                    newapi_user_id=newapi_user_id,
                )
            else:
                # 降级：下周期生效。当前合同照常用到期末，新合同排在其后。
                # 立即降级会把用户已经付过钱的这一期额度砍掉。
                start_at = current.contract_end_at or now
                if start_at <= now:
                    start_at = now
                else:
                    status = 'scheduled'

        contract_no = f'HXC{uuid.uuid4().hex[:20].upper()}'
        end_at = start_at + timedelta(seconds=CYCLE_SECONDS * cycle_count)

        contract = UserSubscription(
            app_code=app_code,
            user_id=order.user_id,
            tier=tier_name,
            subscription_type=billing_cycle,
            monthly_credits=Decimal(0),
            current_credits=Decimal(0),
            used_credits=Decimal(0),
            purchased_credits=Decimal(0),
            billing_cycle_start=start_at,
            billing_cycle_end=end_at,
            subscription_start_date=start_at,
            subscription_end_date=end_at,
            status=status,
            # auto_renew 只表示「是否尝试创建下一张续费订单」，不代表自动发额度：
            # 没有新订单支付成功，就不会有下一份合同，更不会有新周期额度。
            auto_renew=bool((getattr(order, 'extra_data', None) or {}).get('auto_renew', True)),
            max_agents=tier.max_agents or 1,
            contract_no=contract_no,
            offering_key=(getattr(order, 'offering_ref', None) or {}).get('offering_key'),
            plan_key=(getattr(order, 'offering_ref', None) or {}).get('plan_key'),
            contract_start_at=start_at,
            contract_end_at=end_at,
            cycle_seconds=CYCLE_SECONDS,
            cycle_count=cycle_count,
            plan_snapshot={
                'tier': tier_name,
                'display_name': tier.display_name,
                'credits_per_cycle': format_credits(credits_per_cycle),
                'cycle_seconds': CYCLE_SECONDS,
                'cycle_count': cycle_count,
                'max_agents': tier.max_agents,
                'storage_bytes': tier.storage_bytes,
                'price_amount': str(tier.yearly_price if billing_cycle == 'yearly' else tier.monthly_price),
            },
            source_order_no=order.order_no,
            external_subscription_id=contract_no,
            fulfillment_status='pending',
        )
        db.add(contract)
        await db.flush()

        event = await credit_grant_event_service.enqueue(
            db,
            event_type=EVENT_SUBSCRIPTION_ACTIVATE,
            idempotency_key=IdempotencyKeys.subscription_activate(contract_no),
            user_id=order.user_id,
            newapi_user_id=newapi_user_id,
            app_code=app_code,
            credit_amount=credits_per_cycle,
            order_no=order.order_no,
            subscription_id=contract.id,
            contract_no=contract_no,
            payload_extra={
                'external_subscription_id': contract_no,
                'start_at': _rfc3339(start_at),
                'end_at': _rfc3339(end_at),
                'cycle_seconds': CYCLE_SECONDS,
                'cycle_count': cycle_count,
                'wallet_overflow': True,
                'reason': 'subscribe',
            },
        )
        order.fulfillment_status = 'pending'
        order.fulfillment_event_id = event.event_id
        log.info(
            f'[Contract] 合同已建立: contract_no={contract_no} tier={tier_name} '
            f'cycle={billing_cycle} status={status} order_no={order.order_no}'
        )
        return contract

    @staticmethod
    async def _tier_rank(db: AsyncSession, tier_name: str | None, app_code: str) -> int:
        """套餐档位高低，用 sort_order 表达。取不到时按最低档处理。

        「升级立即生效、降级下周期生效」必须有一个确定的高低序，
        否则同一次换档在不同人眼里可能一个算升级一个算降级。
        """
        if not tier_name:
            return -1
        tier = await offering_pricing.get_tier(db, tier_name)
        if tier is None:
            return -1
        return tier.sort_order

    @staticmethod
    async def cancel_auto_renew(db: AsyncSession, *, user_id: int, app_code: str = 'huanxing') -> bool:
        """取消自动续费：只改合同状态，**不提前清空额度**。

        用户已经付过这一期的钱，额度用到期末天经地义；到期后由 NewAPI 按 30 天周期清零。
        云端在这里做任何「立即回收」都是在替 NewAPI 决定余额。
        """
        contract = (
            await db.execute(
                select(UserSubscription)
                .where(
                    UserSubscription.app_code == app_code,
                    UserSubscription.user_id == user_id,
                    UserSubscription.status == 'active',
                )
                .with_for_update()
            )
        ).scalar_one_or_none()
        if contract is None:
            return False
        contract.auto_renew = False
        contract.status = STATUS_CANCEL_AT_PERIOD_END
        log.info(f'[Contract] 已取消自动续费（额度保留至期末）: user_id={user_id} contract_no={contract.contract_no}')
        return True

    @staticmethod
    async def _terminate(
        db: AsyncSession,
        *,
        contract: UserSubscription,
        idempotency_key: str,
        reason: str,
        newapi_user_id: int,
        refund_no: str | None = None,
    ) -> None:
        """终止一份合同：改状态 + 登记订阅到期命令（剩余额度由 NewAPI 清零）。"""
        external_id = contract.external_subscription_id
        if not external_id:
            # 存量合同没有 NewAPI 投影，只收敛云端状态，不发无处可投的命令。
            contract.status = 'expired'
            contract.contract_end_at = timezone.now()
            log.warning(f'[Contract] 合同 {contract.id} 无 NewAPI 投影，仅收敛云端状态')
            return

        contract.status = 'expired'
        contract.contract_end_at = timezone.now()
        contract.fulfillment_status = 'pending'
        await credit_grant_event_service.enqueue(
            db,
            event_type=EVENT_SUBSCRIPTION_EXPIRE,
            idempotency_key=idempotency_key,
            user_id=contract.user_id,
            newapi_user_id=newapi_user_id,
            app_code=contract.app_code,
            refund_no=refund_no,
            subscription_id=contract.id,
            contract_no=contract.contract_no,
            payload_extra={'external_subscription_id': external_id, 'reason': reason},
        )

    @staticmethod
    async def expire_for_refund(db: AsyncSession, *, order: Any, refund_no: str) -> None:
        """订阅退款：在退款单事务内终止合同并登记到期命令。"""
        app_code = (getattr(order, 'extra_data', None) or {}).get('app_code', 'huanxing')
        result = await db.execute(
            select(UserSubscription)
            .where(UserSubscription.source_order_no == order.order_no)
            .order_by(UserSubscription.id.desc())
            .with_for_update()
        )
        contract = result.scalars().first()
        if contract is None:
            raise errors.RequestError(msg=f'订单 {order.order_no} 没有对应合同，无法安全退款回收')

        from backend.app.billing.service.pay_callbacks import _resolve_newapi_user_id

        newapi_user_id = await _resolve_newapi_user_id(db, order.user_id, app_code)
        await SubscriptionContractService._terminate(
            db,
            contract=contract,
            idempotency_key=IdempotencyKeys.refund_subscription_expire(refund_no),
            reason='refund',
            newapi_user_id=newapi_user_id,
            refund_no=refund_no,
        )
        log.info(f'[Contract] 退款终止合同: contract_no={contract.contract_no} refund_no={refund_no}')


subscription_contract_service: SubscriptionContractService = SubscriptionContractService()
