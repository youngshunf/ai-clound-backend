"""通用语音模型签名目录 - CI 发布 API（Bearer 发布密钥）SPCAT-4。

离线发布方（`scripts/package-speech-model.sh --publish`）签好 Ed25519 catalog + 打好 zip 后，
携发布密钥调此端点：zip 落公开桶 + catalog 原文入库 + bump revision（全网 daemon 据 revision 重拉）。
鉴权：Authorization: Bearer <SPEECH_CATALOG_PUBLISH_SECRET>（constant-time 比较，同 hasn_release CI）。
密钥未配置则拒绝所有发布（生产必须显式配置，避免误开放写库）。

云端**只哑存储 + 一致性预检**，不验签、不改写——daemon 持内置公钥自行验签才是安全执行点。
"""

from __future__ import annotations

import hmac

from typing import Annotated

from fastapi import APIRouter, File, Form, Header, UploadFile

from backend.app.hasn.schema.hasn_speech_catalog import SpeechCatalogPublishResponse
from backend.app.hasn.service.speech_catalog_service import speech_catalog_service
from backend.common.exception import errors
from backend.common.response.response_schema import ResponseSchemaModel, response_base
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
    '/publish',
    summary='CI 发布签名语音 catalog + 模型 zip（Bearer 发布密钥）',
    name='hasn_speech_catalog_ci_publish',
)
async def publish_speech_catalog(
    db: CurrentSessionTransaction,
    file: Annotated[UploadFile, File(description='模型分发包 zip（与 catalog 内声明 sha256/URL 一致）')],
    catalog: Annotated[str, Form(description='签名 catalog 逐字节原文 {payload, signature}')],
    object_key: Annotated[str, Form(description='zip 落公开桶的对象 key，如 speech/sensevoice-small-int8/2024-07-17/xxx.zip')],
    authorization: str | None = Header(default=None),
) -> ResponseSchemaModel[SpeechCatalogPublishResponse]:
    """离线发布方一键发布：先把 zip 传公开桶可达、再落 catalog 原文（顺序铁律，绝不让 daemon 下 404）。

    - 鉴权：`Authorization: Bearer <SPEECH_CATALOG_PUBLISH_SECRET>`。
    - 请求体 = multipart：`file`（zip 二进制）+ `catalog`（签名原文文本）+ `object_key`（表单）。
    - 服务端做四项一致性预检（URL 指向 object_key、zip sha256 对拍、https、落桶直链与声明一致）后落库。
    """
    _verify_publish_bearer(authorization)
    zip_bytes = await file.read()
    data = await speech_catalog_service.publish(
        db,
        catalog_json=catalog,
        zip_bytes=zip_bytes,
        object_key=object_key,
        published_by='ci',
    )
    # 语音目录发布 → 主动 push hasn.sync.invalidate(speech_catalog) 给在线节点：
    # 在线 daemon 秒级重拉 /speech-catalog/catalog 验签并落盘，离线节点靠重连握手对账。
    # best-effort，推送失败绝不影响已发布（同 platform_config 范式）。
    try:
        from backend.app.hasn.service.sync_invalidate_service import bump as sync_bump

        await sync_bump('speech_catalog', db)
    except Exception as exc:
        import logging

        logging.getLogger(__name__).warning('[speech_catalog] invalidate 推送失败 (非致命): %s', exc)
    return response_base.success(data=data)
