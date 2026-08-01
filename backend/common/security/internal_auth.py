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


def _require_internal_token(request: Request, *, expected: str, setting_name: str) -> None:
    """按独立配置校验内部服务令牌，禁止空配置绕过。"""
    if not expected:
        raise errors.TokenError(msg=f'{setting_name} 未配置，internal endpoint 不可用')
    provided = request.headers.get(_HEADER_NAME)
    if not provided:
        raise errors.TokenError(msg=f'缺少 {_HEADER_NAME} header')
    if not hmac.compare_digest(provided, expected):
        raise errors.TokenError(msg=f'{_HEADER_NAME} 校验失败')


async def require_runtime_internal_token(request: Request) -> None:
    """校验 X-Internal-Token header == settings.RUNTIME_INTERNAL_TOKEN。

    - 缺 header → 401
    - 错 token → 401
    - 服务端未配置 RUNTIME_INTERNAL_TOKEN → 拒绝（避免空字符串绕过）
    """
    _require_internal_token(
        request,
        expected=settings.RUNTIME_INTERNAL_TOKEN,
        setting_name='RUNTIME_INTERNAL_TOKEN',
    )


async def require_publish_internal_token(request: Request) -> None:
    """校验 Growth → Publish 内部 HTTP 使用的独立服务令牌。"""
    _require_internal_token(
        request,
        expected=settings.PUBLISH_INTERNAL_TOKEN,
        setting_name='PUBLISH_INTERNAL_TOKEN',
    )


async def require_hosting_internal_bearer(request: Request) -> None:
    """校验 edge / hosting-agent → 云端内部面的 `Authorization: Bearer <hosting 服务令牌>`。

    令牌口径与云端调 hosting-agent 时**完全一致**（`service_endpoint('hosting').token`：显式
    env/settings 优先，否则由 `derive_service_token(master_secret, 'hosting')` 派生），两端无需各配。
    令牌为空（主密钥未配且未显式配置）→ 拒绝所有调用，避免空字符串绕过。

    契约：`docs/hasn-node设计文档/云端节点托管/实施/01-切片实施契约(H1-H8).md` §3.3。
    """
    # 局部 import：避免 common.security 在导入期反向依赖服务目录（后者读 settings + services.toml）
    from backend.common.service_registry import service_endpoint

    expected = service_endpoint('hosting').token
    if not expected:
        raise errors.TokenError(msg='hosting 内部服务令牌未配置，internal endpoint 不可用')
    authorization = request.headers.get('Authorization') or ''
    if not authorization.lower().startswith('bearer '):
        raise errors.TokenError(msg='缺少 Bearer 内部服务令牌')
    provided = authorization[7:].strip()
    if not provided or not hmac.compare_digest(provided, expected):
        raise errors.TokenError(msg='hosting 内部服务令牌校验失败')
