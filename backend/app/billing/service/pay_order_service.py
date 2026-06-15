"""支付订单核心 Service — 创建订单、回调处理、状态查询"""

import secrets
import time

from datetime import timedelta
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.billing.core.callback import dispatch_pay_success
from backend.app.billing.core.config import (
    ORDER_EXPIRE_MINUTES,
    PAY_ORDER_NOTIFY_URL,
)
from backend.app.billing.crud.crud_credit_package import credit_package_dao
from backend.app.billing.crud.crud_pay_channel import pay_channel_dao
from backend.app.billing.crud.crud_pay_contract import pay_contract_dao
from backend.app.billing.crud.crud_pay_merchant import pay_merchant_dao
from backend.app.billing.crud.crud_pay_notify_log import pay_notify_log_dao
from backend.app.billing.crud.crud_pay_order import pay_order_dao
from backend.app.billing.crud.crud_subscription_tier import subscription_tier_dao
from backend.app.billing.model.pay_order import PayOrder
from backend.app.billing.schema.pay_order import (
    CreatePayOrderParam,
    CreatePayOrderResponse,
    PayOrderStatusResponse,
)
from backend.app.billing.service.channel.base import PayClient
from backend.common.exception import errors
from backend.common.log import log
from backend.common.pagination import paging_data
from backend.utils.timezone import timezone


def _generate_order_no() -> str:
    ts = int(time.time() * 1000)
    rand = secrets.randbelow(10000)
    return f'HX{ts}{rand:04d}'


def _generate_contract_no() -> str:
    ts = int(time.time() * 1000)
    rand = secrets.randbelow(10000)
    return f'CT{ts}{rand:04d}'


# 客户端缓存（按 channel_id）
_client_cache: dict[int, PayClient] = {}


def _build_client(channel, merchant_config: dict | None = None) -> PayClient:
    """根据渠道编码构建 PayClient

    优先使用传入的 merchant_config，回退 channel.config（兼容旧数据）
    """
    config = merchant_config or channel.config or {}
    if not config:
        raise errors.ServerError(msg=f'渠道 {channel.code} 未配置支付密钥')
    code = channel.code
    # 合并渠道特有配置
    if channel.extra_config:
        config = {**config, **channel.extra_config}
    notify_url = f'{PAY_ORDER_NOTIFY_URL}/{channel.id}'

    if code == 'wx_papay':
        from backend.app.billing.service.channel.wechat_papay import WechatPapayClient
        return WechatPapayClient(config, notify_url)
    elif code.startswith('wx'):
        from backend.app.billing.service.channel.wechat_native import WechatNativeClient
        return WechatNativeClient(config, notify_url)
    elif code == 'alipay_qr':
        # 支付宝当面付（扫码）：出可扫二维码，应用内呈现（桌面端）
        from backend.app.billing.service.channel.alipay_qr import AlipayQrClient
        return AlipayQrClient(config, notify_url)
    elif code.startswith('alipay'):
        from backend.app.billing.service.channel.alipay_pc import AlipayPcClient
        return AlipayPcClient(config, notify_url)
    else:
        raise errors.ServerError(msg=f'不支持的渠道: {code}')


def get_pay_client(channel, merchant_config: dict | None = None, force_new: bool = False) -> PayClient:
    """获取支付客户端（带缓存）"""
    if not force_new and channel.id in _client_cache:
        return _client_cache[channel.id]
    client = _build_client(channel, merchant_config)
    _client_cache[channel.id] = client
    return client


def clear_client_cache(channel_id: int | None = None):
    if channel_id:
        _client_cache.pop(channel_id, None)
    else:
        _client_cache.clear()


class PayOrderService:
    @staticmethod
    async def get(*, db: AsyncSession, pk: int) -> PayOrder:
        order = await pay_order_dao.get(db, pk)
        if not order:
            raise errors.NotFoundError(msg='订单不存在')
        return order

    @staticmethod
    async def get_by_order_no(*, db: AsyncSession, order_no: str) -> PayOrder:
        order = await pay_order_dao.get_by_order_no(db, order_no)
        if not order:
            raise errors.NotFoundError(msg='订单不存在')
        return order

    @staticmethod
    async def get_list(
        db: AsyncSession,
        user_id: int | None = None,
        status: int | None = None,
    ) -> dict[str, Any]:
        select_stmt = await pay_order_dao.get_select(user_id=user_id, status=status)
        return await paging_data(db, select_stmt)

    @staticmethod
    async def get_status(*, db: AsyncSession, order_no: str) -> PayOrderStatusResponse:
        order = await pay_order_dao.get_by_order_no(db, order_no)
        if not order:
            raise errors.NotFoundError(msg='订单不存在')
        return PayOrderStatusResponse(
            order_no=order.order_no,
            status=order.status,
            pay_amount=order.pay_amount,
            success_time=order.success_time,
        )

    @staticmethod
    async def _resolve_channel(db: AsyncSession, channel_code: str):
        """解析渠道 + 商户密钥配置。渠道不可用 → RequestError。"""
        channel = await pay_channel_dao.get_by_code(db, channel_code)
        if not channel or channel.status != 1:
            raise errors.RequestError(msg=f'支付渠道 {channel_code} 不可用')
        merchant_config = None
        if channel.merchant_id:
            merchant = await pay_merchant_dao.get(db, channel.merchant_id)
            if merchant and merchant.status == 1:
                merchant_config = merchant.config
        return channel, merchant_config

    @staticmethod
    def _invoke_channel_create(
        channel,
        merchant_config: dict | None,
        *,
        order_no: str,
        amount: int,
        subject: str,
        body: str,
        user_ip: str | None,
        contract_no: str = '',
    ) -> tuple[str | None, str | None]:
        """调渠道 SDK 下单，返回 (qr_code_url, pay_url)。SDK 失败 → ServerError。"""
        try:
            client = get_pay_client(channel, merchant_config=merchant_config)
            pay_result = client.create_order(
                order_no=order_no, amount=amount, subject=subject,
                body=body, user_ip=user_ip, contract_no=contract_no,
            )
            return pay_result.get('qr_code_url'), pay_result.get('pay_url')
        except Exception as e:
            log.error(f'SDK 下单失败: channel={channel.code}, error={e}', exc_info=True)
            raise errors.ServerError(msg=f'支付渠道调用失败: {e}')

    @staticmethod
    async def create_order(
        *,
        db: AsyncSession,
        user_id: int,
        obj: CreatePayOrderParam,
        user_ip: str | None = None,
        app_code: str = 'huanxing',
    ) -> CreatePayOrderResponse:
        """创建支付订单。据 `order_type` 分支：订阅 / 积分包 / 应用购买（真实支付，零 mock）。"""
        if obj.order_type == 'credit_pack':
            return await PayOrderService._create_credit_pack_order(
                db=db, user_id=user_id, obj=obj, user_ip=user_ip, app_code=app_code
            )
        if obj.order_type == 'app_purchase':
            return await PayOrderService._create_app_purchase_order(
                db=db, user_id=user_id, obj=obj, user_ip=user_ip, app_code=app_code
            )
        return await PayOrderService._create_subscribe_order(
            db=db, user_id=user_id, obj=obj, user_ip=user_ip, app_code=app_code
        )

    @staticmethod
    async def _create_subscribe_order(
        *,
        db: AsyncSession,
        user_id: int,
        obj: CreatePayOrderParam,
        user_ip: str | None,
        app_code: str,
    ) -> CreatePayOrderResponse:
        if obj.billing_cycle not in ('monthly', 'yearly'):
            raise errors.RequestError(msg=f'无效的计费周期: {obj.billing_cycle}')

        # 从数据库读取套餐配置（按 app_code 区分应用）
        tier_config = await subscription_tier_dao.select_model_by_column(
            db, tier_name=obj.tier, app_code=app_code, enabled=True
        )
        if not tier_config:
            raise errors.RequestError(msg=f'无效的套餐: {obj.tier}（app={app_code}）')

        # 从数据库获取价格（单位：元 → 分）
        if obj.billing_cycle == 'yearly' and tier_config.yearly_price:
            pay_amount = int(float(tier_config.yearly_price) * 100)
        else:
            pay_amount = int(float(tier_config.monthly_price) * 100)

        if pay_amount <= 0:
            raise errors.RequestError(msg='免费套餐无需支付')

        tier_name = tier_config.display_name
        cycle_name = '月付' if obj.billing_cycle == 'monthly' else '年付'
        subject = f'唤星AI-{tier_name}会员-{cycle_name}'
        body = f'{tier_name}（{cycle_name}）订阅'

        channel, merchant_config = await PayOrderService._resolve_channel(db, obj.channel_code)

        order_no = _generate_order_no()
        now = timezone.now()
        expire_time = now + timedelta(minutes=ORDER_EXPIRE_MINUTES)

        order_dict = {
            'order_no': order_no,
            'user_id': user_id,
            'channel_id': channel.id,
            'channel_code': channel.code,
            'order_type': 'subscribe',
            'subject': subject,
            'body': body,
            'target_tier': obj.tier,
            'billing_cycle': obj.billing_cycle,
            'amount': pay_amount,
            'pay_amount': pay_amount,
            'expire_time': expire_time,
            'user_ip': user_ip,
            'extra_data': {'app_code': app_code},
        }
        await pay_order_dao.create(db, order_dict)

        contract_no = None
        if obj.auto_renew:
            contract_no = _generate_contract_no()
            contract_dict = {
                'user_id': user_id,
                'channel_code': channel.code,
                'contract_no': contract_no,
                'tier': obj.tier,
                'billing_cycle': obj.billing_cycle,
                'deduct_amount': pay_amount,
                'status': 0,
            }
            await pay_contract_dao.create(db, contract_dict)

        qr_code_url, pay_url = PayOrderService._invoke_channel_create(
            channel, merchant_config,
            order_no=order_no, amount=pay_amount, subject=subject,
            body=body, user_ip=user_ip, contract_no=contract_no or '',
        )

        return CreatePayOrderResponse(
            order_no=order_no, pay_amount=pay_amount, channel_code=channel.code,
            qr_code_url=qr_code_url, pay_url=pay_url, contract_no=contract_no,
            expire_time=expire_time,
        )

    @staticmethod
    async def _create_credit_pack_order(
        *,
        db: AsyncSession,
        user_id: int,
        obj: CreatePayOrderParam,
        user_ip: str | None,
        app_code: str,
    ) -> CreatePayOrderResponse:
        """积分包真实支付下单。积分数写入 `extra_data.credit_amount`，到账由
        `handle_credit_pack_paid` 回调读取并发放（购买积分 `is_purchased=True` 永不过期）。
        """
        package = await credit_package_dao.get(db, obj.package_id)
        if not package or not package.enabled or package.app_code != app_code:
            raise errors.RequestError(msg=f'无效的积分包: {obj.package_id}（app={app_code}）')

        pay_amount = int(float(package.price) * 100)
        if pay_amount <= 0:
            raise errors.RequestError(msg='免费积分包无需支付')

        total_credits = package.credits + package.bonus_credits
        subject = f'唤星AI-积分包-{package.package_name}'
        body = f'{package.package_name}（{package.credits}+{package.bonus_credits} 积分）'

        channel, merchant_config = await PayOrderService._resolve_channel(db, obj.channel_code)

        order_no = _generate_order_no()
        now = timezone.now()
        expire_time = now + timedelta(minutes=ORDER_EXPIRE_MINUTES)

        order_dict = {
            'order_no': order_no,
            'user_id': user_id,
            'channel_id': channel.id,
            'channel_code': channel.code,
            'order_type': 'credit_pack',
            'subject': subject,
            'body': body,
            'target_tier': None,
            'billing_cycle': None,
            'amount': pay_amount,
            'pay_amount': pay_amount,
            'expire_time': expire_time,
            'user_ip': user_ip,
            'extra_data': {
                'app_code': app_code,
                'package_id': package.id,
                'credit_amount': float(total_credits),
            },
        }
        await pay_order_dao.create(db, order_dict)

        qr_code_url, pay_url = PayOrderService._invoke_channel_create(
            channel, merchant_config,
            order_no=order_no, amount=pay_amount, subject=subject,
            body=body, user_ip=user_ip,
        )

        return CreatePayOrderResponse(
            order_no=order_no, pay_amount=pay_amount, channel_code=channel.code,
            qr_code_url=qr_code_url, pay_url=pay_url, contract_no=None,
            expire_time=expire_time,
        )

    @staticmethod
    async def _create_app_purchase_order(
        *,
        db: AsyncSession,
        user_id: int,
        obj: CreatePayOrderParam,
        user_ip: str | None,
        app_code: str,
    ) -> CreatePayOrderResponse:
        """AI-Native 应用购买真实下单（设计 §5.4）。

        从 `hasn_app_catalog` 取价（须 published + access_type=purchase + 有价）；app_id 写入
        `extra_data.app_id`，到账由 `handle_app_purchase_paid` 回调读取并写 owner 维度权益。
        已有有效权益的不重复下单（幂等前置，避免重复扣费）。
        """
        # 延迟导入避免 pay → hasn 顶层循环依赖（与 user_tier dao 同为业务定价来源）。
        from backend.app.hasn_core.app_platform import app_catalog_service

        catalog = await app_catalog_service.get_published_catalog(db, app_id=obj.app_id)
        if catalog is None:
            raise errors.RequestError(msg=f'应用不存在或已下架: {obj.app_id}')
        if (catalog.access_type or 'free') != 'purchase':
            raise errors.RequestError(msg='该应用不是购买型，无需下单')
        if catalog.price_amount is None or float(catalog.price_amount) <= 0:
            raise errors.RequestError(msg='该应用未配置购买价格')

        owner_hasn_id = await app_catalog_service.resolve_owner_hasn_id(db, user_id=user_id)
        if owner_hasn_id and await app_catalog_service.get_active_entitlement(
            db, app_id=catalog.app_id, subject_type='owner', subject_id=owner_hasn_id
        ):
            raise errors.RequestError(msg='已拥有该应用，无需重复购买')

        pay_amount = int(float(catalog.price_amount) * 100)
        subject = f'唤星AI-应用购买-{catalog.name}'
        body = f'购买应用「{catalog.name}」'

        channel, merchant_config = await PayOrderService._resolve_channel(db, obj.channel_code)

        order_no = _generate_order_no()
        now = timezone.now()
        expire_time = now + timedelta(minutes=ORDER_EXPIRE_MINUTES)

        order_dict = {
            'order_no': order_no,
            'user_id': user_id,
            'channel_id': channel.id,
            'channel_code': channel.code,
            'order_type': 'app_purchase',
            'subject': subject,
            'body': body,
            'target_tier': None,
            # catalog.billing_cycle（once/month/year）→ 回调据此算权益到期。
            'billing_cycle': catalog.billing_cycle,
            'amount': pay_amount,
            'pay_amount': pay_amount,
            'expire_time': expire_time,
            'user_ip': user_ip,
            'extra_data': {'app_code': app_code, 'app_id': catalog.app_id},
        }
        await pay_order_dao.create(db, order_dict)

        qr_code_url, pay_url = PayOrderService._invoke_channel_create(
            channel, merchant_config,
            order_no=order_no, amount=pay_amount, subject=subject,
            body=body, user_ip=user_ip,
        )

        return CreatePayOrderResponse(
            order_no=order_no, pay_amount=pay_amount, channel_code=channel.code,
            qr_code_url=qr_code_url, pay_url=pay_url, contract_no=None,
            expire_time=expire_time,
        )

    @staticmethod
    async def cancel_order(*, db: AsyncSession, order_no: str, user_id: int) -> None:
        order = await pay_order_dao.get_by_order_no(db, order_no)
        if not order:
            raise errors.NotFoundError(msg='订单不存在')
        if order.user_id != user_id:
            raise errors.ForbiddenError(msg='无权操作此订单')
        if order.status != 0:
            raise errors.RequestError(msg='订单状态不允许取消')
        await pay_order_dao.update_status(db, order_no, status=3)

    @staticmethod
    async def handle_pay_notify(
        *,
        db: AsyncSession,
        order_no: str,
        channel_order_no: str,
        pay_amount: int,
        channel_code: str,
        channel_user_id: str | None = None,
        raw_data: str | None = None,
    ) -> bool:
        await pay_notify_log_dao.create(db, {
            'notify_type': 'pay',
            'order_no': order_no,
            'channel_code': channel_code,
            'notify_data': raw_data,
            'status': 0,
        })

        order = await pay_order_dao.get_by_order_no_for_update(db, order_no)
        if not order:
            raise errors.NotFoundError(msg=f'订单 {order_no} 不存在')

        if order.status == 1:
            return False

        if order.pay_amount != pay_amount:
            raise errors.RequestError(msg=f'金额不一致: 订单 {order.pay_amount} vs 回调 {pay_amount}')

        now = timezone.now()
        await pay_order_dao.update_status(
            db, order_no=order_no, status=1,
            channel_order_no=channel_order_no,
            channel_user_id=channel_user_id, success_time=now,
        )

        await dispatch_pay_success(order.order_type, order)
        return True

    @staticmethod
    async def expire_timeout_orders(*, db: AsyncSession) -> int:
        return await pay_order_dao.expire_timeout_orders(db)


pay_order_service: PayOrderService = PayOrderService()
