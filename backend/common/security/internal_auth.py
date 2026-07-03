"""内部 service token 认证（runtime ↔ backend 单向调用）。

用于 §09 §5 中的 hermes internal endpoints：
- runtime 调 backend 的 internal endpoint 时必须携带 X-Internal-Token header
- token 不暴露给浏览器，只在服务进程间使用
- 与 JWT/Agent Key 完全分离，不复用 USER_JWT 系列

用法::

    from backend.common.security.internal_auth import require_runtime_internal_token

    @router.post('/foo', dependencies=[Depends(require_runtime_internal_token)])
    async def foo(...):
        ...
"""
from __future__ import annotations

import hmac

from fastapi import Request

from backend.common.exception import errors
from backend.core.conf import settings

_HEADER_NAME = 'X-Internal-Token'


async def require_runtime_internal_token(request: Request) -> None:
    """校验 X-Internal-Token header == settings.RUNTIME_INTERNAL_TOKEN。

    - 缺 header → 401
    - 错 token → 401
    - 服务端未配置 RUNTIME_INTERNAL_TOKEN → 拒绝（避免空字符串绕过）
    """
    expected = settings.RUNTIME_INTERNAL_TOKEN
    if not expected:
        raise errors.TokenError(msg='RUNTIME_INTERNAL_TOKEN 未配置，internal endpoint 不可用')

    provided = request.headers.get(_HEADER_NAME)
    if not provided:
        raise errors.TokenError(msg=f'缺少 {_HEADER_NAME} header')

    if not hmac.compare_digest(provided, expected):
        raise errors.TokenError(msg='X-Internal-Token 校验失败')
