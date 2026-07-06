"""桌面端发布 - 公开 API（官网 Hero / 下载页 / Tauri updater 消费，无需认证）。

- GET /latest              官网 Hero + 下载页：当前 channel 最新版本 + 各平台 installer（信封）
- GET /releases            历史版本列表（信封）
- GET /updater/{target}/{arch}/{current_version}  Tauri v2 updater manifest（裸 JSON；有更新 200 / 无更新 204）
- GET /download/{asset_id} 下载计数重定向（302 → 七牛 CDN）

updater 与 download 是「统一信封根本满足不了」的真例外：
  - updater 返回 Tauri 原生 JSON 形状（客户端按原生解析），且无更新时 204 无体；
  - download 是 302 重定向到 CDN。
两者已登记进 test_response_envelope_contract 的 KNOWN_NON_ENVELOPE 白名单。
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Path, Query, Response
from fastapi.responses import RedirectResponse

from backend.app.hasn_release.schema.release import LatestReleaseResponse, ReleaseDetail
from backend.app.hasn_release.service.release_service import release_service
from backend.common.response.response_schema import ResponseSchemaModel, response_base
from backend.database.db import CurrentSession

router = APIRouter()


@router.get('/latest', summary='最新版本 + 各平台安装包（官网 Hero/下载页）', name='hasn_release_open_latest')
async def get_latest(
    db: CurrentSession,
    channel: Annotated[str, Query(description='stable/beta')] = 'stable',
) -> ResponseSchemaModel[LatestReleaseResponse]:
    data = await release_service.get_latest(db, channel=channel)
    return response_base.success(data=data)


@router.get('/releases', summary='历史版本列表', name='hasn_release_open_releases')
async def list_releases(
    db: CurrentSession,
    channel: Annotated[str | None, Query(description='stable/beta，空=全部')] = None,
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
) -> ResponseSchemaModel[list[ReleaseDetail]]:
    data = await release_service.list_releases(db, channel=channel, limit=limit)
    return response_base.success(data=data)


@router.get(
    '/updater/{target}/{arch}/{current_version}',
    summary='Tauri v2 updater manifest（有更新 200 / 无更新 204）',
    name='hasn_release_open_updater',
)
async def tauri_updater(
    db: CurrentSession,
    target: Annotated[str, Path(description='平台 darwin/windows/linux')],
    arch: Annotated[str, Path(description='架构 aarch64/x86_64')],
    current_version: Annotated[str, Path(description='客户端当前版本')],
    channel: Annotated[str, Query(description='stable/beta')] = 'stable',
) -> Response:
    manifest = await release_service.build_updater_manifest(
        db, target=target, arch=arch, current_version=current_version, channel=channel
    )
    if manifest is None:
        # Tauri 约定：无更新回 204 No Content
        return Response(status_code=204)
    return Response(
        content=manifest.model_dump_json(),
        media_type='application/json',
        status_code=200,
    )


@router.get(
    '/download/{asset_id}',
    summary='下载计数重定向（302 → 七牛 CDN）',
    name='hasn_release_open_download',
)
async def download(
    db: CurrentSession,
    asset_id: Annotated[int, Path(description='release_asset.id')],
) -> RedirectResponse:
    url = await release_service.resolve_download(db, asset_id)
    return RedirectResponse(url=url, status_code=302)
