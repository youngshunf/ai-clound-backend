import logging

from typing import Annotated

from fastapi import APIRouter, Depends, Path, Query

from backend.app.hasn.schema.hasn_app_catalog import (
    CreateHasnAppCatalogParam,
    DeleteHasnAppCatalogParam,
    GetHasnAppCatalogDetail,
    UpdateHasnAppCatalogParam,
)
from backend.app.hasn.service.hasn_app_catalog_service import hasn_app_catalog_service
from backend.common.pagination import DependsPagination, PageData
from backend.common.response.response_schema import ResponseModel, ResponseSchemaModel, response_base
from backend.common.security.jwt import DependsJwtAuth
from backend.common.security.permission import RequestPermission
from backend.common.security.rbac import DependsRBAC
from backend.database.db import CurrentSession, CurrentSessionTransaction

log = logging.getLogger(__name__)

router = APIRouter()


@router.get('/{pk}', summary='获取AI-Native 应用目录（云端权威）详情', dependencies=[DependsJwtAuth], name='admin_get_hasn_app_catalog')
async def get_hasn_app_catalog(
    db: CurrentSession, pk: Annotated[int, Path(description='AI-Native 应用目录（云端权威） ID')]
) -> ResponseSchemaModel[GetHasnAppCatalogDetail]:
    hasn_app_catalog = await hasn_app_catalog_service.get(db=db, pk=pk)
    return response_base.success(data=hasn_app_catalog)


@router.get(
    '',
    summary='分页获取所有AI-Native 应用目录（云端权威）',
    dependencies=[
        DependsJwtAuth,
        DependsPagination,
    ],
    name='admin_get_hasn_app_catalog_paginated',
)
async def get_hasn_app_catalog_paginated(db: CurrentSession) -> ResponseSchemaModel[PageData[GetHasnAppCatalogDetail]]:
    page_data = await hasn_app_catalog_service.get_list(db=db)
    return response_base.success(data=page_data)


@router.post(
    '',
    summary='创建AI-Native 应用目录（云端权威）',
    dependencies=[
        Depends(RequestPermission('hasn:app:catalog:add')),
        DependsRBAC,
    ],
    name='admin_create_hasn_app_catalog',
)
async def create_hasn_app_catalog(db: CurrentSessionTransaction, obj: CreateHasnAppCatalogParam) -> ResponseModel:
    await hasn_app_catalog_service.create(db=db, obj=obj)
    return response_base.success()


@router.put(
    '/{pk}',
    summary='更新AI-Native 应用目录（云端权威）',
    dependencies=[
        Depends(RequestPermission('hasn:app:catalog:edit')),
        DependsRBAC,
    ],
    name='admin_update_hasn_app_catalog',
)
async def update_hasn_app_catalog(
    db: CurrentSessionTransaction, pk: Annotated[int, Path(description='AI-Native 应用目录（云端权威） ID')], obj: UpdateHasnAppCatalogParam
) -> ResponseModel:
    count = await hasn_app_catalog_service.update(db=db, pk=pk, obj=obj)
    if count > 0:
        # 应用目录配置（含 config_json，如 film 视频引擎 5 类模型）变更 → push hasn.sync.invalidate
        # (platform_config) 给在线节点（FILMCFG-1：config_json 经 platform-config 通道下发的 app_configs）。
        # 在线 daemon 秒级重拉并应用——这是「编辑配置→保存→改模型名即生效」真正下发到桌面端的环。
        # best-effort：推送失败绝不影响配置已写入；离线节点靠重连握手对账追平。
        try:
            from backend.app.hasn.service.sync_invalidate_service import bump as sync_bump

            await sync_bump('platform_config', db)
        except Exception as e:
            log.warning(f'[HASN] app_catalog 变更 platform_config invalidate 推送失败 (非致命): {e}')
        return response_base.success()
    return response_base.fail()


@router.delete(
    '',
    summary='批量删除AI-Native 应用目录（云端权威）',
    dependencies=[
        Depends(RequestPermission('hasn:app:catalog:del')),
        DependsRBAC,
    ],
    name='admin_delete_hasn_app_catalog',
)
async def delete_hasn_app_catalog(db: CurrentSessionTransaction, obj: DeleteHasnAppCatalogParam) -> ResponseModel:
    count = await hasn_app_catalog_service.delete(db=db, obj=obj)
    if count > 0:
        return response_base.success()
    return response_base.fail()
