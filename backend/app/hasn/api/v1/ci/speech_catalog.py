"""通用语音模型签名目录 - CI 原子发布 API（Bearer 发布密钥）SPCAT-4。

离线发布方先逐包暂存内容寻址 ZIP，再提交引用全部已暂存对象的签名 release。
鉴权：Authorization: Bearer <SPEECH_CATALOG_PUBLISH_SECRET>（constant-time 比较，同 hasn_release CI）。
密钥未配置则拒绝所有发布（生产必须显式配置，避免误开放写库）。

云端**只哑存储 + 一致性预检**，不验签、不改写——daemon 持内置公钥自行验签才是安全执行点。
"""

from __future__ import annotations

import hmac

from typing import Annotated

from fastapi import APIRouter, File, Form, Header, UploadFile

from backend.app.hasn.schema.hasn_speech_catalog import (
    SpeechCatalogPublishResponse,
    SpeechPackageStageResponse,
)
from backend.app.hasn.service.speech_catalog_service import speech_catalog_service
from backend.common.exception import errors
from backend.common.response.response_code import CustomResponseCode
from backend.common.response.response_schema import ResponseSchemaModel
from backend.core.conf import settings
from backend.database.db import CurrentSessionTransaction

router = APIRouter()


def _verify_publish_bearer(authorization: str | None) -> None:
    """校验发布密钥（constant-time，未配置一律拒绝）。"""
    secret = (settings.SPEECH_CATALOG_PUBLISH_SECRET or '').strip()
    if not secret:
        raise errors.ForbiddenError(msg='语音目录发布密钥未配置（SPEECH_CATALOG_PUBLISH_SECRET），拒绝发布')
    token = ''
    if authorization and authorization.lower().startswith('bearer '):
        token = authorization[7:].strip()
    if not token or not hmac.compare_digest(token, secret):
        raise errors.ForbiddenError(msg='语音目录发布鉴权失败')


@router.post(
    '/packages',
    summary='CI 暂存内容寻址语音模型包（Bearer 发布密钥）',
    name='hasn_speech_catalog_ci_stage_package',
)
async def stage_speech_package(
    db: CurrentSessionTransaction,
    file: Annotated[UploadFile, File(description='模型分发包 ZIP，服务端据原始字节派生 SHA-256 与对象 key')],
    authorization: Annotated[str | None, Header()] = None,
) -> ResponseSchemaModel[SpeechPackageStageResponse]:
    """暂存一个真实 ZIP；同摘要重复上传返回同一不可变登记。"""
    _verify_publish_bearer(authorization)
    package_bytes = await file.read()
    data = await speech_catalog_service.stage_package(
        db,
        package_bytes=package_bytes,
        content_type=file.content_type,
    )
    success = CustomResponseCode.HTTP_200
    return ResponseSchemaModel[SpeechPackageStageResponse](
        code=success.code,
        msg=success.msg,
        data=data,
    )


@router.post(
    '/releases',
    summary='CI 原子发布签名语音 catalog release（Bearer 发布密钥）',
    name='hasn_speech_catalog_ci_publish_release',
)
async def publish_speech_catalog_release(
    db: CurrentSessionTransaction,
    catalog: Annotated[str, Form(description='完整 v2 签名 catalog 逐字节原文')],
    authorization: Annotated[str | None, Header()] = None,
) -> ResponseSchemaModel[SpeechCatalogPublishResponse]:
    """证明全部引用对象可达后，在同一事务内写 release、映射并切换权威 head。

    - 鉴权：`Authorization: Bearer <SPEECH_CATALOG_PUBLISH_SECRET>`。
    - 请求体 = multipart：`catalog`（完整 v2 签名原文文本）。
    - 同序列同原文幂等；回退序列或同序列不同原文显式拒绝。
    """
    _verify_publish_bearer(authorization)
    data = await speech_catalog_service.publish_release(
        db,
        catalog_json=catalog,
        published_by='ci',
    )
    # 语音目录发布 → 主动 push hasn.sync.invalidate(speech_catalog) 给在线节点：
    # 在线 daemon 秒级重拉 /speech-catalog/catalog 验签并落盘，离线节点靠重连握手对账。
    # best-effort，推送失败绝不影响已发布（同 platform_config 范式）。
    try:
        from backend.app.hasn.service.sync_invalidate_service import bump as sync_bump

        if not data.idempotent:
            await sync_bump('speech_catalog', db)
    except Exception as exc:
        import logging

        logging.getLogger(__name__).warning('[speech_catalog] invalidate 推送失败 (非致命): %s', exc)
    success = CustomResponseCode.HTTP_200
    return ResponseSchemaModel[SpeechCatalogPublishResponse](
        code=success.code,
        msg=success.msg,
        data=data,
    )
