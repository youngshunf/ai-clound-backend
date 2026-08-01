"""routing delivery bus 的归属约束测试。"""

import inspect

from backend.app.hasn_im.adapters.routing import delivery_bus


def test_delivery_bus_is_implemented_inside_routing_layer() -> None:
    """跨 worker 投递总线必须由 routing 层自身实现，不能代理遗留模块。"""
    assert delivery_bus.WsDeliveryBus.__module__ == delivery_bus.__name__
    assert isinstance(delivery_bus.ws_delivery_bus, delivery_bus.WsDeliveryBus)


def test_delivery_claim_supports_redis_6_lua_and_redis_8_lmove() -> None:
    """领取待投帧同时保留 Redis 6 Lua 与 Redis 8 LMOVE 路径。"""
    source = inspect.getsource(delivery_bus)

    assert '.lmove(' in source
    assert '_MOVE_PENDING_TO_PROCESSING_LUA' in source
