"""桌面端发布 - CI 回调 API（Bearer CI 密钥）。

GitHub Actions 出包 + 上传七牛后回调此端点落库（source=github）。
鉴权：Authorization: Bearer <RELEASE_CI_CALLBACK_SECRET>（constant-time 比较）。
密钥未配置则拒绝所有回调（生产必须显式配置，避免误开放写库）。
"""

from __future__ import annotations

import hmac

from typing import Annotated

from fastapi import APIRouter, Header, Path, Query, Request

from backend.app.hasn_release.schema.release import (
    CiCallbackRequest,
    CiUploadResponse,
    ConfirmReleaseTagRequest,
    HeadlessImageDetail,
    HeadlessImageRequest,
    PrepareReleaseRequest,
    ReleaseBatchResponse,
    ReleaseDetail,
)
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


@router.post(
    '/prepare',
    summary='创建或加入云端桌面端发布批次',
    name='hasn_release_ci_prepare',
)
async def prepare_release(
    db: CurrentSessionTransaction,
    obj: PrepareReleaseRequest,
    authorization: Annotated[str | None, Header()] = None,
) -> ResponseSchemaModel[ReleaseBatchResponse]:
    _verify_ci_bearer(authorization)
    data = await release_service.prepare_release(db, obj)
    return response_base.success(data=data)


@router.get(
    '/batches/{release_id}',
    summary='查询云端桌面端发布批次状态',
    name='hasn_release_ci_get_batch',
)
async def get_release_batch(
    db: CurrentSessionTransaction,
    release_id: Annotated[int, Path(gt=0)],
    authorization: Annotated[str | None, Header()] = None,
) -> ResponseSchemaModel[ReleaseBatchResponse]:
    _verify_ci_bearer(authorization)
    data = await release_service.get_release_batch(db, release_id)
    return response_base.success(data=data)


@router.post(
    '/batches/{release_id}/confirm-tag',
    summary='核验 release tag 并由 LLM 生成更新说明',
    name='hasn_release_ci_confirm_tag',
)
async def confirm_release_tag(
    db: CurrentSessionTransaction,
    release_id: Annotated[int, Path(gt=0)],
    obj: ConfirmReleaseTagRequest,
    authorization: Annotated[str | None, Header()] = None,
) -> ResponseSchemaModel[ReleaseBatchResponse]:
    _verify_ci_bearer(authorization)
    data = await release_service.confirm_release_tag(db, release_id, obj)
    return response_base.success(data=data)


@router.post('/upload', summary='CI 上传产物到公共桶（Bearer CI 密钥）', name='hasn_release_ci_upload')
async def ci_upload(
    request: Request,
    db: CurrentSessionTransaction,
    version: Annotated[str, Query(description='semver，如 1.2.0')],
    file_name: Annotated[str, Query(description='原始文件名，如 唤星_1.2.0_aarch64.dmg')],
    channel: Annotated[str, Query(description='stable/beta')] = 'stable',
    release_id: Annotated[int | None, Query(gt=0, description='云端发布批次 app_release.id')] = None,
    authorization: Annotated[str | None, Header()] = None,
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
        release_id=release_id,
        content_type=request.headers.get('content-type'),
    )
    return response_base.success(data=data)


@router.post('/callback', summary='CI 构建完成回调（Bearer CI 密钥）', name='hasn_release_ci_callback')
async def ci_callback(
    db: CurrentSessionTransaction,
    obj: CiCallbackRequest,
    authorization: Annotated[str | None, Header()] = None,
) -> ResponseSchemaModel[ReleaseDetail]:
    _verify_ci_bearer(authorization)
    data = await release_service.ci_callback(db, obj)
    return response_base.success(data=data)


@router.post(
    '/headless-image',
    summary='登记无头 hasn-node 容器镜像（Bearer CI 密钥）',
    name='hasn_release_ci_register_headless_image',
)
async def register_headless_image(
    db: CurrentSessionTransaction,
    obj: HeadlessImageRequest,
    authorization: Annotated[str | None, Header()] = None,
) -> ResponseSchemaModel[HeadlessImageDetail]:
    """`release-headless-node.sh` 推私有 registry 后回调此端点，以 **digest** 登记镜像。

    只 upsert 一条 `asset_kind='image'` 资产：不动 `is_latest`、不动桌面端资产、不改批次状态——
    桌面端发布与自动更新链路行为完全不变（契约 §7「只加不改既有桌面 target 行为」）。
    """
    _verify_ci_bearer(authorization)
    data = await release_service.register_headless_image(db, obj)
    return response_base.success(data=data)
