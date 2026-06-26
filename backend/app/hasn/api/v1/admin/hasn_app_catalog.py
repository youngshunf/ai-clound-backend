import logging

from typing import Annotated

from fastapi import APIRouter, Depends, File, Form, Path, UploadFile

from backend.app.hasn.schema.hasn_app_catalog import (
    CreateHasnAppCatalogParam,
    DeleteHasnAppCatalogParam,
    GetHasnAppCatalogDetail,
    UpdateHasnAppCatalogConfigParam,
    UpdateHasnAppCatalogParam,
)
from backend.app.hasn.service import app_catalog_service
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


@router.put(
    '/{pk}/config',
    summary='仅更新应用专属平台级配置 JSON（管理端「编辑配置」，只改 config_json）',
    dependencies=[
        Depends(RequestPermission('hasn:app:catalog:edit')),
        DependsRBAC,
    ],
    name='admin_update_hasn_app_catalog_config',
)
async def update_hasn_app_catalog_config(
    db: CurrentSessionTransaction,
    pk: Annotated[int, Path(description='AI-Native 应用目录（云端权威） ID')],
    obj: UpdateHasnAppCatalogConfigParam,
) -> ResponseModel:
    # 管理端「编辑配置」只改 config_json 一项（如 reel/film 引擎模型名），不应被迫回填整行
    # （app_id/name/icon…），否则会撞 UpdateHasnAppCatalogParam 全字段必填校验报「app_id 字段为必填项」。
    count = await hasn_app_catalog_service.update_config(db=db, pk=pk, config_json=obj.config_json)
    if count > 0:
        # 与全字段 update 一致：config_json 变更 → push hasn.sync.invalidate(platform_config)，
        # 在线 daemon 秒级重拉并应用——这是「编辑配置→保存→改模型名即生效」下发到桌面端的环。
        # best-effort：推送失败绝不影响配置已写入；离线节点靠重连握手对账追平。
        try:
            from backend.app.hasn.service.sync_invalidate_service import bump as sync_bump

            await sync_bump('platform_config', db)
        except Exception as e:
            log.warning(f'[HASN] app_catalog config 变更 platform_config invalidate 推送失败 (非致命): {e}')
        return response_base.success()
    return response_base.fail()


@router.post(
    '/{pk}/engine-package',
    summary='上传 downloadable_local 引擎分发包并写入 config_json.engine（FILMPUB 一键发布）',
    dependencies=[
        Depends(RequestPermission('hasn:app:catalog:edit')),
        DependsRBAC,
    ],
    name='admin_publish_engine_package',
)
async def publish_engine_package(
    db: CurrentSessionTransaction,
    pk: Annotated[int, Path(description='AI-Native 应用目录（云端权威） ID')],
    file: Annotated[UploadFile, File(description='引擎分发包 zip（顶层含 backend/）')],
    os_arch: Annotated[str, Form(description='目标架构，如 darwin-aarch64 / linux-x86_64')],
    version: Annotated[str, Form(description='引擎版本（多架构须同版本）')],
    sha256: Annotated[str | None, Form(description='客户端算的 sha256，交叉校验上传完整性')] = None,
) -> ResponseSchemaModel[dict]:
    # 服务端权威算 sha256/size → 落公共桶 → 并入 config_json.engine → sync_bump（push 全网 daemon）。
    # 顺序：先上传可达再写配置，绝不让 daemon 去下 404（详见 service.publish_engine_package）。
    data = await file.read()
    engine = await app_catalog_service.publish_engine_package(
        db,
        pk=pk,
        os_arch=os_arch,
        version=version,
        data=data,
        filename=file.filename or f'film-{os_arch}-{version}.zip',
        expected_sha256=sha256,
    )
    return response_base.success(data=engine)


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
