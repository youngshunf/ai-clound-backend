"""community_ext 路由 HTTP 装配守卫（实施/95 Phase 6 回归防护）。

背景：四个 community_ext 路由曾把 `CurrentSession` / `AgentTokenPayload` 等放在
`if TYPE_CHECKING:` 块里，配合 `from __future__ import annotations`，FastAPI 在
注册路由时 `get_type_hints` 解析不到运行期名字 → 要么导入即 NameError 崩应用，
要么把 `db` 当成必填 query 参数（线上 422）。service 级单测调服务函数，绕过了
路由签名解析，漏掉了这个 HTTP 级 bug。本测试在不起服务的前提下导入四个 router，
断言：①能成功导入（依赖名运行期可解析）；②没有把 `db`/session 泄露成 query 参数。
"""

from __future__ import annotations

import importlib

from fastapi.routing import APIRoute

_EXT_ROUTER_MODULES = [
    'backend.app.hasn_community.api.v1.open.community_ext',
    'backend.app.hasn_community.api.v1.app.community_ext',
    'backend.app.hasn_community.api.v1.agent.community_ext',
    'backend.app.hasn_community.api.v1.admin.community_ext',
]

# session 依赖一旦被当成 query 参数，就是 TYPE_CHECKING-only 导入回归。
_LEAK_NAMES = {'db'}


def test_community_ext_routers_import_and_resolve_session_as_dependency() -> None:
    total = 0
    leaks: list[tuple[str, str, str]] = []
    for module_path in _EXT_ROUTER_MODULES:
        module = importlib.import_module(module_path)
        routes = [r for r in module.router.routes if isinstance(r, APIRoute)]
        assert routes, f'{module_path} 未注册任何路由'
        for route in routes:
            total += 1
            query_names = {p.name for p in route.dependant.query_params}
            for leak in _LEAK_NAMES & query_names:
                leaks.append((module_path, route.path, leak))
    assert total >= 50, f'community_ext 路由数异常偏少：{total}'
    assert not leaks, f'session 依赖被当成 query 参数（TYPE_CHECKING-only 导入回归）：{leaks}'
