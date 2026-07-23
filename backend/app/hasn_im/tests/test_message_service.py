"""消息应用服务归属测试。"""

from backend.app.hasn_im.application import message_service


def test_message_routing_is_implemented_inside_hasn_im() -> None:
    """消息路由实现必须由 hasn_im 应用层拥有。"""
    assert message_service.route_message.__module__ == message_service.__name__
    assert message_service.persist_message.__module__ == message_service.__name__
