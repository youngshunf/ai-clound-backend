"""Agent JWT 路由必须从 Owner JWT 中间件放行（守卫，项目硬规则）。

**规则**：任何带 `DependsAgentJwtAuth` 的路由，其路径必须命中
`settings.TOKEN_REQUEST_PATH_EXCLUDE` / `TOKEN_REQUEST_PATH_EXCLUDE_PATTERN`。

为什么：全局 `JwtAuthMiddleware` 对所有带 `Authorization: Bearer` 的请求按
**Owner JWT** 解析，而 Agent JWT 的 `sub` 是 `a_*`（非数字 user_id），
`jwt_decode` 里 `int(sub)` 必抛 → 401「Token 已失效，请重新登录」——请求根本
到不了路由自己的 `DependsAgentJwtAuth`。历史上 Agent 面用 `X-Agent-Key`
（无 Authorization 头）时中间件天然跳过，迁移到 Agent JWT Bearer 后任何
遗漏白名单的新路由都会在真实 HTTP 下整面 401（2026-06-11 任务系统 M8 live
E2E 抓到：/hasn-task/app/runs/summary、/notifications/agent/*、
/community/agent/*、/ai-native/runtime/*、/ai-native/audit/report 五面全军覆没，
而 in-process service 层测试因绕过中间件全绿）。

新增 Agent JWT 路由时：在 `backend/core/conf.py` 的
`TOKEN_REQUEST_PATH_EXCLUDE_PATTERN` 增加对应（尽量精确的）模式。
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from fastapi.routing import APIRoute

from backend.common.security.agent_jwt_auth import agent_jwt_auth
from backend.core.conf import settings
from backend.plugin.core import build_final_router

if TYPE_CHECKING:
    from collections.abc import Callable

    from fastapi.dependencies.models import Dependant


def _dependant_uses(dependant: Dependant, target: Callable) -> bool:
    if dependant.call is target:
        return True
    return any(_dependant_uses(sub, target) for sub in dependant.dependencies)


def _is_middleware_excluded(path: str) -> bool:
    if path in settings.TOKEN_REQUEST_PATH_EXCLUDE:
        return True
    return any(pattern.match(path) for pattern in settings.TOKEN_REQUEST_PATH_EXCLUDE_PATTERN)


def _collect_agent_jwt_routes() -> list[str]:
    router = build_final_router()
    return [
        route.path
        for route in router.routes
        if isinstance(route, APIRoute) and _dependant_uses(route.dependant, agent_jwt_auth)
    ]


def test_agent_jwt_routes_exist() -> None:
    """自检：路由内省能找到 Agent JWT 面（找不到说明检测逻辑失效，守卫形同虚设）。"""
    paths = _collect_agent_jwt_routes()
    assert paths, 'route introspection found no DependsAgentJwtAuth routes — guard is broken'


def test_agent_jwt_routes_are_middleware_excluded() -> None:
    """每条 Agent JWT 路由都必须被 Owner JWT 中间件白名单放行。

    路径模板里的 `{param}` 不影响匹配：白名单用 `.*`/前缀模式，模板字面量
    （如 `/runs/summary`）按字面匹配即可；带参数段的路由应使用模式放行。
    """
    offenders = [path for path in _collect_agent_jwt_routes() if not _is_middleware_excluded(path)]
    assert not offenders, (
        'Agent JWT (Bearer) 路由未从 Owner JWT 中间件放行，真实 HTTP 下必 401：\n'
        + '\n'.join(f'  {p}' for p in offenders)
        + '\n→ 在 backend/core/conf.py TOKEN_REQUEST_PATH_EXCLUDE_PATTERN 增加对应模式。'
    )
