"""routing delivery bus 的归属约束测试。"""

from backend.app.hasn_im.adapters.routing import delivery_bus


def test_delivery_bus_is_implemented_inside_routing_layer() -> None:
    """跨 worker 投递总线必须由 routing 层自身实现，不能代理遗留模块。"""
    assert delivery_bus.WsDeliveryBus.__module__ == delivery_bus.__name__
    assert isinstance(delivery_bus.ws_delivery_bus, delivery_bus.WsDeliveryBus)
