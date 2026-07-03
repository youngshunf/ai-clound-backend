"""回归守卫：MCP Streamable 接入面必须绕过全局 Owner-JWT 中间件。

`/api/v1/mcp/streamable` 由 handler 内部 `_authenticate_from_headers` 自鉴权
（Agent MCP Key `hasn_amk_` / Agent JWT），不能走 `JwtAuthMiddleware` 的 Owner-JWT
校验——否则中间件会先把非 Owner-JWT 的 key 当成 Owner JWT 解码失败、返回
`401 Token 无效`，请求根本到不了 handler（P2 用 pytest 直调 handler 绕过了中间件，
没暴露这条；2026-06-01 活体联调发现）。此测试锁住该路径在白名单内。
"""

from backend.core.conf import settings


def _is_jwt_exempt(path: str) -> bool:
    if path in settings.TOKEN_REQUEST_PATH_EXCLUDE:
        return True
    return any(pattern.match(path) for pattern in settings.TOKEN_REQUEST_PATH_EXCLUDE_PATTERN)


def test_mcp_streamable_bypasses_owner_jwt_middleware() -> None:
    path = f'{settings.FASTAPI_API_V1_PATH}/mcp/streamable'
    assert _is_jwt_exempt(path), (
        'MCP Streamable 必须在 JWT 白名单内（handler 自鉴权 Agent MCP Key / JWT）；'
        '否则全局 Owner-JWT 中间件会先以 401 Token 无效拒掉 key 请求'
    )
