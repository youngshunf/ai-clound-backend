"""联系人写路由的 RelationGateway 装配回归。

旧测试把 API 内部 DAO 和实时推送逐层替换，实际无法证明生产写路径使用 IM 角色。R3
收口后，本文件改为验证 FastAPI 的真实依赖图：所有关系写路由必须注入统一
``get_relation_gateway``，模块不得缓存跨事件循环的全局 gateway。
"""

from __future__ import annotations

from fastapi.routing import APIRoute

from backend.app.hasn.api.v1.app import contacts
from backend.app.hasn_im.application.provider import get_relation_gateway


_WRITE_ROUTES = {
    ('/contacts/request', 'POST'),
    ('/contacts/requests/{request_id}/respond', 'PUT'),
    ('/contacts/{contact_id}/trust-level', 'PUT'),
    ('/contacts/{contact_id}', 'DELETE'),
    ('/contacts/{contact_id}/permissions', 'PUT'),
}


def _route_dependencies(route: APIRoute) -> set[object]:
    """递归收集路由依赖 callable。"""
    dependencies: set[object] = set()
    stack = list(route.dependant.dependencies)
    while stack:
        dependency = stack.pop()
        if dependency.call is not None:
            dependencies.add(dependency.call)
        stack.extend(dependency.dependencies)
    return dependencies


def test_all_contact_write_routes_inject_relation_gateway() -> None:
    """每个公开关系写口都必须经请求级 RelationGateway 依赖。"""
    matched: set[tuple[str, str]] = set()
    for route in contacts.router.routes:
        if not isinstance(route, APIRoute):
            continue
        for method in route.methods:
            key = (route.path, method)
            if key not in _WRITE_ROUTES:
                continue
            matched.add(key)
            assert get_relation_gateway in _route_dependencies(route)
    assert matched == _WRITE_ROUTES


def test_contacts_module_has_no_global_relation_gateway() -> None:
    """禁止模块级缓存带连接池的 gateway，避免跨事件循环复用连接。"""
    assert not hasattr(contacts, '_relation_gateway')
