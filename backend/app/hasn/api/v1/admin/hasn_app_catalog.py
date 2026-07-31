import json
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
from backend.common.exception import errors
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


@router.post(
    '/{pk}/finance-engine-release',
    summary='发布 Finance Ed25519 签名引擎清单 v2',
    dependencies=[
        Depends(RequestPermission('hasn:app:catalog:edit')),
        DependsRBAC,
    ],
    name='admin_publish_finance_engine_release',
)
async def publish_finance_engine_release(
    db: CurrentSessionTransaction,
    pk: Annotated[int, Path(description='finance 应用目录（云端权威） ID')],
    manifest: Annotated[UploadFile, File(description='Ed25519 签名 manifest.json v2')],
) -> ResponseSchemaModel[dict]:
    document = await manifest.read(app_catalog_service.MAX_FINANCE_RELEASE_MANIFEST_BYTES + 1)
    release = await app_catalog_service.publish_finance_engine_release(
        db,
        pk=pk,
        document=document,
    )
    return response_base.success(data=release)


@router.post(
    '/{pk}/engine-package-stage',
    summary='上传 schema v2 待签引擎包，不切换在线清单',
    dependencies=[
        Depends(RequestPermission('hasn:app:catalog:edit')),
        DependsRBAC,
    ],
    name='admin_stage_signed_engine_package',
)
async def stage_signed_engine_package(
    db: CurrentSessionTransaction,
    pk: Annotated[int, Path(description='AI-Native 应用目录（云端权威） ID')],
    file: Annotated[UploadFile, File(description='待签引擎 zip（包内含逐文件清单）')],
    os_arch: Annotated[str, Form(description='规范平台键，如 macos-aarch64 / linux-x86_64')],
    version: Annotated[str, Form(description='引擎版本')],
    sha256: Annotated[str, Form(description='客户端 sha256；服务端权威复算并交叉校验')],
) -> ResponseSchemaModel[dict]:
    package = await app_catalog_service.stage_signed_engine_package(
        db,
        pk=pk,
        os_arch=os_arch,
        version=version,
        data=await file.read(),
        filename=file.filename or f'imagelab-{os_arch}-{version}.zip',
        expected_sha256=sha256,
    )
    return response_base.success(data=package)


@router.post(
    '/{pk}/engine-manifest',
    summary='发布 schema v2 Ed25519 签名引擎清单并原子切换在线配置',
    dependencies=[
        Depends(RequestPermission('hasn:app:catalog:edit')),
        DependsRBAC,
    ],
    name='admin_publish_signed_engine_manifest',
)
async def publish_signed_engine_manifest(
    db: CurrentSessionTransaction,
    pk: Annotated[int, Path(description='AI-Native 应用目录（云端权威） ID')],
    manifest: Annotated[UploadFile, File(description='schema v2 签名 manifest.json')],
) -> ResponseSchemaModel[dict]:
    raw = await manifest.read()
    if len(raw) > 8 * 1024 * 1024:
        raise errors.RequestError(msg='图坊签名 manifest 超过 8 MiB 上限')
    try:
        document = json.loads(raw)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise errors.RequestError(msg='图坊签名 manifest 不是合法 UTF-8 JSON') from exc
    engine = await app_catalog_service.publish_signed_engine_manifest(
        db,
        pk=pk,
        document=document,
    )
    return response_base.success(data=engine)


@router.post(
    '/{pk}/model-package-stage',
    summary='上传 schema v1 待签模型包，不切换在线目录',
    dependencies=[
        Depends(RequestPermission('hasn:app:catalog:edit')),
        DependsRBAC,
    ],
    name='admin_stage_signed_model_package',
)
async def stage_signed_model_package(
    # 模型包上传耗时以分钟计，服务内部会在远程 I/O 前显式释放事务，故不用事务型会话依赖。
    db: CurrentSession,
    pk: Annotated[int, Path(description='AI-Native 应用目录（云端权威） ID')],
    file: Annotated[UploadFile, File(description='模型 zip（内含单个 .onnx）')],
    runtime_name: Annotated[str, Form(description='引擎识别的稳定运行时模型名，如 birefnet-general')],
    version: Annotated[str, Form(description='模型发布版本')],
) -> ResponseSchemaModel[dict]:
    package = await app_catalog_service.stage_signed_model_package(
        db,
        pk=pk,
        runtime_name=runtime_name,
        version=version,
        upload=file,
    )
    return response_base.success(data=package)


@router.post(
    '/{pk}/model-catalog',
    summary='发布 schema v1 Ed25519 签名模型目录并原子切换在线配置',
    dependencies=[
        Depends(RequestPermission('hasn:app:catalog:edit')),
        DependsRBAC,
    ],
    name='admin_publish_signed_model_catalog',
)
async def publish_signed_model_catalog(
    db: CurrentSessionTransaction,
    pk: Annotated[int, Path(description='AI-Native 应用目录（云端权威） ID')],
    catalog: Annotated[UploadFile, File(description='schema v1 签名模型目录 json')],
) -> ResponseSchemaModel[dict]:
    raw = await catalog.read()
    if len(raw) > 8 * 1024 * 1024:
        raise errors.RequestError(msg='图坊模型目录超过 8 MiB 上限')
    try:
        document = json.loads(raw)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise errors.RequestError(msg='图坊模型目录不是合法 UTF-8 JSON') from exc
    models = await app_catalog_service.publish_signed_model_catalog(db, pk=pk, document=document)
    return response_base.success(data=models)


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
