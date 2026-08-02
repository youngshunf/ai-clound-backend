"""用户内容按需翻译 API（app scope，Owner JWT）。

路由前缀 `/api/v1/hasn/app`，即：

- `POST /api/v1/hasn/app/content/translate`        单条资源、可多字段
- `POST /api/v1/hasn/app/content/translate/batch`  批量（≤ 20 条）

WebUI **不直接调这里**，走 daemon `/api/v1/community/translate` 代理（WebUI 只调 daemon 铁律）。

入参只收「资源类型 + 资源 ID + 字段名 + 目标语言」，**不收原文**：原文由服务端从权威表取，
否则这个接口就成了任何人都能白嫖的免费翻译器。
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Request
from pydantic import BaseModel, Field

from backend.app.hasn.service.content_translation_service import (
    RESOURCE_FIELDS,
    content_translation_service,
)
from backend.common.exception import errors
from backend.common.log import log
from backend.common.response.response_schema import ResponseSchemaModel, response_base
from backend.common.security.jwt import DependsJwtAuth
from backend.core.conf import settings
from backend.database.db import CurrentSession

router = APIRouter()


class TranslateContentRequest(BaseModel):
    """单条翻译请求。**没有 text 字段**——原文由服务端自己取。"""

    resource_kind: str = Field(..., description=f'资源类型，可选: {list(RESOURCE_FIELDS)}')
    resource_id: str = Field(..., min_length=1, max_length=64, description='资源的云端权威 ID')
    fields: list[str] = Field(default_factory=lambda: ['content'], description='要翻译的字段名')
    target_lang: str = Field(..., min_length=2, max_length=16, description='目标语言，如 en / ja / zh-TW')


class TranslateBatchItem(BaseModel):
    resource_kind: str
    resource_id: str = Field(..., min_length=1, max_length=64)
    fields: list[str] = Field(default_factory=lambda: ['content'])


class TranslateBatchRequest(BaseModel):
    """批量翻译请求（列表页翻若干条摘要）。"""

    items: list[TranslateBatchItem] = Field(..., min_length=1, description='待翻资源列表')
    target_lang: str = Field(..., min_length=2, max_length=16)


async def _viewer_hasn_id(db: CurrentSession, request: Request) -> str:
    """解析登录 Owner 的 hasn_id（身份=认证凭证，不读请求体身份）。"""
    from backend.app.hasn_core import hasn_humans_dao

    human = await hasn_humans_dao.get_by_user_id(db, request.user.id)
    if not human:
        raise errors.NotFoundError(msg='用户 HASN 身份不存在')
    return str(human.hasn_id)


@router.post(
    '/content/translate',
    summary='翻译用户内容（单条，多字段）',
    name='hasn_app_content_translate',
    dependencies=[DependsJwtAuth],
)
async def translate_content(
    request: Request,
    db: CurrentSession,
    obj: TranslateContentRequest,
) -> ResponseSchemaModel[dict[str, Any]]:
    viewer_hasn_id = await _viewer_hasn_id(db, request)
    data = await content_translation_service.translate_resource(
        db,
        resource_kind=obj.resource_kind,
        resource_id=obj.resource_id,
        fields=obj.fields,
        target_lang=obj.target_lang,
        viewer_hasn_id=viewer_hasn_id,
    )
    return response_base.success(data=data)


@router.post(
    '/content/translate/batch',
    summary='翻译用户内容（批量，最多 20 条）',
    name='hasn_app_content_translate_batch',
    dependencies=[DependsJwtAuth],
)
async def translate_content_batch(
    request: Request,
    db: CurrentSession,
    obj: TranslateBatchRequest,
) -> ResponseSchemaModel[dict[str, Any]]:
    """批量翻译。

    单条失败**不拖垮整批**：每条各自带 `ok` 与 `error`，前端按条渲染。但失败条目
    仍然只给错误、**不给原文**——列表里混进未翻译的原文而不标注，用户会以为那条
    就是译文（零 fake）。
    """
    max_items = settings.CONTENT_TRANSLATION_BATCH_MAX_ITEMS
    if len(obj.items) > max_items:
        raise errors.RequestError(msg=f'批量翻译单次最多 {max_items} 条（本次 {len(obj.items)} 条）')

    viewer_hasn_id = await _viewer_hasn_id(db, request)

    async def run(item: TranslateBatchItem) -> dict[str, Any]:
        try:
            data = await content_translation_service.translate_resource(
                db,
                resource_kind=item.resource_kind,
                resource_id=item.resource_id,
                fields=item.fields,
                target_lang=obj.target_lang,
                viewer_hasn_id=viewer_hasn_id,
            )
            return {'ok': True, **data}
        except Exception as exc:
            # 逐条容错：一条 403/404 不该让另外 19 条也失败。按日志规范记 warn。
            log.warning(
                f'[content-translate] 批量条目失败 {item.resource_kind}/{item.resource_id}: {exc}'
            )
            return {
                'ok': False,
                'resource_kind': item.resource_kind,
                'resource_id': item.resource_id,
                'error': getattr(exc, 'msg', None) or str(exc),
            }

    # 共用同一个 db 会话，串行执行——AsyncSession 不是并发安全的，
    # 并发跑会撞 "another operation is in progress"。
    results = []
    for item in obj.items:
        results.append(await run(item))

    return response_base.success(
        data={'target_lang': obj.target_lang, 'items': results}
    )
