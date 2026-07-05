"""通用网页发布与分享 公开元数据面（/api/v1/publish/open）。

给 website `/s/{slug}` SPA 查看器判定渲染态用：可见性 / 是否需口令 / 是否需登录 / 是否可用。
带 `/api/v1` 前缀（走 CORS，前端 fetch 调）；只读元数据，**不泄露** owner_id/asset_id/私有桶信息。
不存在/撤销 slug 探测按 IP 限速（与 hosting 共享 probe 窗口，防枚举）。
设计事实源：docs/hasn-node设计文档/18-通用网页发布与分享/实施/03-分享查看器迁移website与官网重新定位.md（阶段 B）。
"""

from __future__ import annotations

from fastapi import APIRouter, Request

from backend.app.hasn_publish.api.v1.open.hosting import (
    _PROBE_MAX,
    _PROBE_WINDOW_SECONDS,
    _client_ip,
    _rate_limited,
)
from backend.app.hasn_publish.service.publish_service import publish_service
from backend.common.exception import errors
from backend.common.response.response_schema import ResponseModel, response_base
from backend.database.db import CurrentSession

router = APIRouter()


@router.get('/sites/{slug}/meta', summary='分享公开元数据（查看器判定渲染态）')
async def get_share_meta(request: Request, db: CurrentSession, slug: str) -> ResponseModel:
    """website /s/{slug} 查看器首帧调用，判定渲染 public/unlisted/password/private 哪种态。

    不存在/已撤销 → 404（探测限速防枚举）；过期/无当前版本 → available=False（查看器出诚实空态）；
    private → requires_login=True：查看器引导登录换票（仅发布者本人可见）。
    """
    site = await publish_service.get_site_by_slug(db, slug=slug)
    if site is None or site.status == 'revoked':
        if await _rate_limited(f'publish:probe:{_client_ip(request)}', window=_PROBE_WINDOW_SECONDS, limit=_PROBE_MAX):
            raise errors.RequestError(code=429, msg='请求过于频繁')
        raise errors.NotFoundError(msg='分享不存在或已撤销')
    expired = publish_service.is_expired(site)
    return response_base.success(
        data={
            'slug': site.slug,
            'title': site.title,
            'kind': site.kind,
            'visibility': site.visibility,
            'has_password': bool(site.password_hash),
            'requires_login': site.visibility == 'private',
            'allow_present': site.allow_present,
            'allow_download': site.allow_download,
            'expired': expired,
            'available': (not expired) and site.current_revision_id is not None,
        }
    )
