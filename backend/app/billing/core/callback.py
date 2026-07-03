"""业务回调注册机制 — 支付模块不直接依赖业务模块

使用方式：
    # 在 user_tier 模块启动时注册
    from backend.app.billing.core.callback import register_pay_callback
    register_pay_callback('subscribe', handle_subscribe_paid)
    register_pay_callback('credit_pack', handle_credit_pack_paid)
"""

from collections.abc import Callable, Coroutine
from typing import Any

from backend.common.log import log

# 支付成功回调: order_type -> async handler(order)
_pay_callbacks: dict[str, Callable[..., Coroutine[Any, Any, None]]] = {}

# 退款成功回调: order_type -> async handler(order)
_refund_callbacks: dict[str, Callable[..., Coroutine[Any, Any, None]]] = {}


def register_pay_callback(order_type: str, handler: Callable[..., Coroutine[Any, Any, None]]) -> None:
    """注册支付成功业务回调"""
    _pay_callbacks[order_type] = handler
    log.info(f'[pay] 注册支付回调: order_type={order_type}, handler={handler.__name__}')


def register_refund_callback(order_type: str, handler: Callable[..., Coroutine[Any, Any, None]]) -> None:
    """注册退款成功业务回调"""
    _refund_callbacks[order_type] = handler
    log.info(f'[pay] 注册退款回调: order_type={order_type}, handler={handler.__name__}')


async def dispatch_pay_success(order_type: str, order: Any) -> bool:
    """分发支付成功事件到业务模块

    :return: True=有处理器且执行成功, False=无处理器
    """
    handler = _pay_callbacks.get(order_type)
    if handler:
        try:
            await handler(order)
            return True
        except Exception as e:
            log.error(f'[pay] 业务回调异常: order_type={order_type}, error={e}')
            raise
    else:
        log.warning(f'[pay] 未注册的订单类型回调: order_type={order_type}')
        return False


async def dispatch_refund_success(order_type: str, order: Any) -> bool:
    """分发退款成功事件到业务模块"""
    handler = _refund_callbacks.get(order_type)
    if handler:
        try:
            await handler(order)
            return True
        except Exception as e:
            log.error(f'[pay] 退款回调异常: order_type={order_type}, error={e}')
            raise
    else:
        log.warning(f'[pay] 未注册的退款回调: order_type={order_type}')
        return False
