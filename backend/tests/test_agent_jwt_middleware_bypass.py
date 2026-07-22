"""Agent JWT（Bearer）整类放行：由 token 类型分流，不依赖路径白名单（守卫，项目硬规则）。

**机制**：全局 `JwtAuthMiddleware` 对所有带 `Authorization: Bearer` 的请求默认按
**Owner JWT** 解析，而 Agent JWT 的 `sub` 是 `a_*`（非数字 user_id），Owner 解析里
`int(sub)` 必抛 → 401「Token 已失效，请重新登录」，请求根本到不了路由的
`DependsAgentJwtAuth`。

历史修法是把每条 Agent JWT 路由逐一加进 `TOKEN_REQUEST_PATH_EXCLUDE_PATTERN`，靠人
记得不漏配（2026-06-11 任务系统 M8 live E2E 抓到 9 面整面 401，而 in-process service
层测试因绕过中间件全绿）。现改为 `extract_token` 用 `is_agent_token`（不验签读
`token_type`）按 token 类型分流放行——任何 Agent JWT 面无论路径都自动放行，无需再维护
路径白名单；真验签 + Redis 吊销检查仍由路由的 `verify_agent_token` 完成。

本守卫钉住该不变量：Agent Bearer 在任意非公开路径都被 `extract_token` 放行（返回 None，
交路由验签），Owner Bearer 仍被中间件接管。
"""

from __future__ import annotations

from jose import jwt
from starlette.authentication import BaseUser
from starlette.requests import Request

from backend.app.admin.schema.user import GetUserInfoWithRelationDetail
from backend.common.security.agent_jwt import is_agent_token
from backend.core.conf import settings
from backend.middleware.jwt_auth_middleware import JwtAuthMiddleware

# extract_token 只做不验签的类型分流，exp 不参与判定；用固定远期时间戳保持测试无时钟依赖。
_FAR_FUTURE_EXP = 9999999999


def _encode(payload: dict) -> str:
    return jwt.encode(payload, settings.TOKEN_SECRET_KEY, settings.TOKEN_ALGORITHM)


def _agent_token() -> str:
    return _encode({'sub': 'a_test_agent', 'token_type': 'agent', 'exp': _FAR_FUTURE_EXP})


def _owner_token() -> str:
    return _encode({'sub': '123', 'exp': _FAR_FUTURE_EXP})


def _make_request(path: str, auth_header: str | None) -> Request:
    headers = []
    if auth_header is not None:
        headers.append((b'authorization', auth_header.encode()))
    scope = {
        'type': 'http',
        'method': 'GET',
        'path': path,
        'raw_path': path.encode(),
        'query_string': b'',
        'headers': headers,
        'scheme': 'http',
        'server': ('testserver', 80),
        'client': ('testclient', 12345),
        'root_path': '',
    }
    return Request(scope)


def test_is_agent_token_classifies_correctly() -> None:
    """token_type=agent → True；Owner / 垃圾 / 空串 → False。"""
    assert is_agent_token(_agent_token()) is True
    assert is_agent_token(_owner_token()) is False
    assert is_agent_token('not.a.jwt') is False
    assert is_agent_token('') is False


def test_extract_token_bypasses_agent_bearer_on_any_path() -> None:
    """Agent Bearer 在任意非公开路径都放行（返回 None），无需逐条路径白名单。

    下列路径正是历史上必须逐条加白名单、漏一条就整面 401 的 9 面代表；外加一条
    「未来新增 Agent 面」证明零配置即放行。
    """
    api = settings.FASTAPI_API_V1_PATH
    token = _agent_token()
    for path in (
        f'{api}/hasn-task/app/runs/summary',
        f'{api}/hasn-task/agent/tasks',
        f'{api}/notifications/agent/list',
        f'{api}/community/agent/posts',
        f'{api}/deck/agent/decks',
        f'{api}/integration/agent/bindings',
        f'{api}/ai-native/runtime/run',
        f'{api}/ai-native/audit/report',
        f'{api}/some/brand/new/agent/face',  # 未来新增 Agent JWT 面：零配置即放行
    ):
        request = _make_request(path, f'Bearer {token}')
        assert JwtAuthMiddleware.extract_token(request) is None, f'agent bearer 未放行：{path}'


def test_extract_token_keeps_owner_bearer() -> None:
    """Owner Bearer 仍被中间件接管（extract_token 返回原 token，进 Owner 鉴权）。"""
    api = settings.FASTAPI_API_V1_PATH
    token = _owner_token()
    request = _make_request(f'{api}/some/owner/route', f'Bearer {token}')
    assert JwtAuthMiddleware.extract_token(request) == token


def test_extract_token_none_without_authorization() -> None:
    """无 Authorization 头（如 X-Agent-Key 面）天然不进 Owner 鉴权。"""
    api = settings.FASTAPI_API_V1_PATH
    request = _make_request(f'{api}/some/owner/route', None)
    assert JwtAuthMiddleware.extract_token(request) is None


def test_owner_jwt_user_implements_starlette_user_contract() -> None:
    """Owner JWT 解析出的用户可直接交给 Starlette 认证中间件。"""
    user = GetUserInfoWithRelationDetail.model_validate({
        'id': 42,
        'uuid': 'aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa',
        'username': 'owner',
        'nickname': '主人',
        'status': 1,
        'is_superuser': False,
        'is_staff': False,
        'is_multi_login': True,
        'join_time': '2026-07-23T00:00:00+00:00',
        'roles': [],
    })

    assert isinstance(user, BaseUser)
    assert user.is_authenticated is True
    assert user.identity == '42'
    assert user.display_name == '主人'
