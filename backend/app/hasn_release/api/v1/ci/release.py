"""桌面端发布 - CI 回调 API（Bearer CI 密钥）。

GitHub Actions 出包 + 上传七牛后回调此端点落库（source=github）。
鉴权：Authorization: Bearer <RELEASE_CI_CALLBACK_SECRET>（constant-time 比较）。
密钥未配置则拒绝所有回调（生产必须显式配置，避免误开放写库）。
"""

from __future__ import annotations

import hmac

from typing import Annotated

from fastapi import APIRouter, Header, Query, Request

from backend.app.hasn_release.schema.release import CiCallbackRequest, CiUploadResponse, ReleaseDetail
from backend.app.hasn_release.service.release_service import release_service
from backend.common.exception import errors
from backend.common.response.response_schema import ResponseSchemaModel, response_base
from backend.core.conf import settings
from backend.database.db import CurrentSessionTransaction

router = APIRouter()


def _verify_ci_bearer(authorization: str | None) -> None:
    secret = (settings.RELEASE_CI_CALLBACK_SECRET or '').strip()
    if not secret:
        raise errors.ForbiddenError(msg='CI 回调密钥未配置（RELEASE_CI_CALLBACK_SECRET），拒绝回调')
    token = ''
    if authorization and authorization.lower().startswith('bearer '):
        token = authorization[7:].strip()
    if not token or not hmac.compare_digest(token, secret):
        raise errors.ForbiddenError(msg='CI 回调鉴权失败')


@router.post('/upload', summary='CI 上传产物到公共桶（Bearer CI 密钥）', name='hasn_release_ci_upload')
async def ci_upload(
    request: Request,
    db: CurrentSessionTransaction,
    version: Annotated[str, Query(description='semver，如 1.2.0')],
    file_name: Annotated[str, Query(description='原始文件名，如 唤星_1.2.0_aarch64.dmg')],
    channel: Annotated[str, Query(description='stable/beta')] = 'stable',
    authorization: str | None = Header(default=None),
) -> ResponseSchemaModel[CiUploadResponse]:
    """CI 出包后把二进制交云端，复用云端已配置的七牛公共桶（CI 无需任何七牛凭据）。

    - 鉴权同 callback：`Authorization: Bearer <RELEASE_CI_CALLBACK_SECRET>`。
    - 请求体 = 产物二进制裸字节（`Content-Type: application/octet-stream`）；元数据走 query。
    - 返回 CDN https 直链 + sha256 + size，供 CI 组装 ci-callback 的 `ReleaseAssetInput`。
    """
    _verify_ci_bearer(authorization)
    body = await request.body()
    data = await release_service.ci_upload_asset(
        db,
        data=body,
        filename=file_name,
        version=version,
        channel=channel,
        content_type=request.headers.get('content-type'),
    )
    return response_base.success(data=data)


@router.post('/callback', summary='CI 构建完成回调（Bearer CI 密钥）', name='hasn_release_ci_callback')
async def ci_callback(
    db: CurrentSessionTransaction,
    obj: CiCallbackRequest,
    authorization: str | None = Header(default=None),
) -> ResponseSchemaModel[ReleaseDetail]:
    _verify_ci_bearer(authorization)
    data = await release_service.ci_callback(db, obj)
    return response_base.success(data=data)
