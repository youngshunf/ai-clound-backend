"""平台默认配置 - 管理端（运营 Admin 改一处，全平台桌面端自动应用）。

单行权威：GET 读当前生效配置（含出厂默认兜底）+ revision；PUT 覆盖式整体提交（server 重算 revision）。
节点媒体模型（image/tts/stt）+ agent 运行时四槽默认（main/fast/vision/delegation）。
"""

import logging

from fastapi import APIRouter, Depends, Request

from backend.app.hasn.schema.hasn_platform_default_config import (
    PlatformDefaultConfig,
    PlatformDefaultConfigResponse,
    UpdatePlatformDefaultConfigRequest,
)
from backend.app.hasn.service.platform_default_config_service import platform_default_config_service
from backend.common.response.response_schema import ResponseSchemaModel, response_base
from backend.common.security.jwt import DependsJwtAuth
from backend.common.security.permission import RequestPermission
from backend.common.security.rbac import DependsRBAC
from backend.database.db import CurrentSession, CurrentSessionTransaction

log = logging.getLogger(__name__)

router = APIRouter()


@router.get(
    '',
    summary='获取平台默认配置（云端权威单行）',
    dependencies=[DependsJwtAuth],
    name='admin_get_platform_default_config',
)
async def get_platform_default_config(db: CurrentSession) -> ResponseSchemaModel[PlatformDefaultConfigResponse]:
    data = await platform_default_config_service.get_response(db)
    return response_base.success(data=data)


@router.put(
    '',
    summary='更新平台默认配置（覆盖式，server 重算 revision）',
    dependencies=[
        Depends(RequestPermission('hasn:platform:config:edit')),
        DependsRBAC,
    ],
    name='admin_update_platform_default_config',
)
async def update_platform_default_config(
    request: Request, db: CurrentSessionTransaction, obj: UpdatePlatformDefaultConfigRequest
) -> ResponseSchemaModel[PlatformDefaultConfigResponse]:
    updated_by = str(getattr(request.user, 'username', None) or getattr(request.user, 'id', '') or '') or None
    data = await platform_default_config_service.update_config(
        db, config=PlatformDefaultConfig.model_validate(obj.model_dump()), updated_by=updated_by
    )
    # 平台默认配置变更 → 主动 push hasn.sync.invalidate(platform_config) 给在线节点（doc02-07 M2）：
    # 在线 daemon 秒级重拉 /platform-config 并应用，离线节点靠重连握手对账。best-effort，
    # 推送失败绝不影响配置已写入。
    try:
        from backend.app.hasn.service.sync_invalidate_service import bump as sync_bump

        await sync_bump('platform_config', db)
    except Exception as e:
        log.warning(f'[HASN] platform_config invalidate 推送失败 (非致命): {e}')
    return response_base.success(data=data)
