"""云端节点「节点面」API（`/api/v1/hasn/node/cloud`）。

两个端点，服务对象都是**容器里的 daemon**：

- `POST /authorization-code/exchange`：无任何既有身份，凭一次性授权码换设备级 token（契约 §3.2）。
  这是全站少数几个无 Owner JWT 的写端点之一，必须限流，且失败只回三个既定错误码。
- `POST /session-grant/verify`：daemon 拿到 edge 转交的 grant 后回云端核销（契约 §3.4⑥）。
  用容器自己的设备 token 鉴权；**跨用户 grant 一律拒**（D-18 第一道墙，daemon 侧还有第二道）。
"""

from __future__ import annotations

from fastapi import APIRouter, Request
from fastapi.security.utils import get_authorization_scheme_param

from backend.app.hasn_hosting.constants import ERR_RATE_LIMITED
from backend.app.hasn_hosting.schema.cloud_node import (
    ExchangeAuthorizationCodeRequest,
    ExchangeAuthorizationCodeResponse,
    VerifySessionGrantRequest,
    VerifySessionGrantResponse,
)
from backend.app.hasn_hosting.service.access_ticket_service import access_ticket_service
from backend.app.hasn_hosting.service.node_credential_service import node_credential_service
from backend.common.exception import errors
from backend.common.log import log
from backend.common.response.response_schema import ResponseSchemaModel, response_base
from backend.common.response.response_code import StandardResponseCode
from backend.common.security.jwt import jwt_authentication
from backend.database.db import CurrentSessionTransaction

router = APIRouter()


def _client_ip(request: Request) -> str:
    """取调用方 IP（优先反代透传的 X-Forwarded-For 首段）。"""
    forwarded = request.headers.get('X-Forwarded-For') or ''
    if forwarded:
        return forwarded.split(',')[0].strip()
    return request.client.host if request.client else 'unknown'


@router.post(
    '/authorization-code/exchange',
    summary='云端节点授权码兑换设备凭据',
    name='hasn_hosting_node_exchange_authorization_code',
)
async def exchange_authorization_code(
    request: Request,
    db: CurrentSessionTransaction,
    obj: ExchangeAuthorizationCodeRequest,
) -> ResponseSchemaModel[ExchangeAuthorizationCodeResponse]:
    """原子一次性兑换（§3.2）。失败分别回 `code_not_found` / `code_expired` / `code_consumed`。"""
    credential, failure = await node_credential_service.exchange(
        db, code=obj.code, node_id=obj.node_id, client_ip=_client_ip(request)
    )
    if credential is None:
        assert failure is not None
        code = (
            StandardResponseCode.HTTP_429
            if failure.error == ERR_RATE_LIMITED
            else StandardResponseCode.HTTP_400
        )
        raise errors.RequestError(code=code, msg=failure.message, data={'error': failure.error})
    return response_base.success(
        data=ExchangeAuthorizationCodeResponse(
            access_token=credential.access_token,
            refresh_token=credential.refresh_token,
            expires_in=credential.expires_in,
            user_id=credential.user_id,
            owner_hasn_id=credential.owner_hasn_id,
            node_id=credential.node_id,
        )
    )


@router.post(
    '/session-grant/verify',
    summary='校验 edge 转交的会话授予',
    name='hasn_hosting_node_verify_session_grant',
)
async def verify_session_grant(
    request: Request,
    obj: VerifySessionGrantRequest,
) -> ResponseSchemaModel[VerifySessionGrantResponse]:
    """校验签名 / exp / typ 且 jti 一次性，返回 `{node_id, owner_hasn_id, user_id}`。

    鉴权用容器自己的设备 token；再核对「grant 的 user_id == 调用方 user_id」——
    否则 A 的 grant 就能在 B 的节点上换出 A 的归属信息（既是越权也是信息泄露）。
    daemon 收到三元组后**仍要**自校验 node_id / owner_hasn_id，那是第二道墙。
    """
    scheme, credentials = get_authorization_scheme_param(request.headers.get('Authorization'))
    if scheme.lower() != 'bearer' or not credentials:
        raise errors.TokenError(msg='缺少节点设备凭据')
    user_info = await jwt_authentication(credentials)

    claims, error = await access_ticket_service.verify_grant(obj.grant)
    if claims is None:
        assert error is not None
        raise errors.RequestError(code=StandardResponseCode.HTTP_401, msg='会话授予无效', data={'error': error})

    if claims.user_id != user_info.id:
        # 跨用户核销：不变量破坏级的越权尝试 → error
        log.error(
            f'[hosting] 跨用户 session grant 核销被拒: grant_user={claims.user_id}, caller_user={user_info.id}'
        )
        raise errors.ForbiddenError(msg='会话授予与调用方不匹配', data={'error': 'session_grant_owner_mismatch'})

    node_id_header = request.headers.get('X-Node-Id')
    if node_id_header and node_id_header != claims.node_id:
        log.error(
            f'[hosting] 跨节点 session grant 核销被拒: grant_node={claims.node_id}, caller_node={node_id_header}'
        )
        raise errors.ForbiddenError(msg='会话授予与调用节点不匹配', data={'error': 'session_grant_node_mismatch'})

    return response_base.success(
        data=VerifySessionGrantResponse(
            node_id=claims.node_id,
            owner_hasn_id=claims.owner_hasn_id,
            user_id=claims.user_id,
        )
    )

