"""素材站目录平台管理面（A-P2-0 admin，事实源 01-分身资源检索与素材站工具设计 §4.5/§7）。

平台运营在 Vben Admin 配置素材站（pexels/pixabay/coverr/…）：注册、写 api_key、启用/停用、
配 failover priority、维护下载白名单域名。api_key **明文只进不出**——写入加密落库，读回只给
掩码 + 是否已配（对齐 external_mcp secret / newapi APIKey）。

鉴权：Admin JWT + RBAC（RequestPermission）。一律统一信封（ResponseModel + response_base.success）。
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Path

from backend.app.hasn_stock.schema.stock_provider import CreateProviderParam, UpdateProviderParam
from backend.app.hasn_stock.service.provider_store import stock_provider_store
from backend.common.exception import errors
from backend.common.response.response_schema import ResponseModel, response_base
from backend.common.security.jwt import DependsJwtAuth
from backend.common.security.permission import RequestPermission
from backend.common.security.rbac import DependsRBAC

router = APIRouter()


@router.get('/providers', summary='[Admin] 列出素材站目录', dependencies=[DependsJwtAuth])
async def list_stock_providers() -> ResponseModel:
    data = await stock_provider_store.list_admin()
    return response_base.success(data=data)


@router.get('/providers/{provider_id}', summary='[Admin] 素材站详情', dependencies=[DependsJwtAuth])
async def get_stock_provider(provider_id: Annotated[int, Path()]) -> ResponseModel:
    data = await stock_provider_store.get_admin(provider_id)
    if data is None:
        raise errors.NotFoundError(msg='素材站不存在')
    return response_base.success(data=data)


@router.post(
    '/providers',
    summary='[Admin] 新增素材站（api_key 加密落库，不回显）',
    dependencies=[Depends(RequestPermission('hasn_stock:provider:add')), DependsRBAC],
)
async def create_stock_provider(obj: CreateProviderParam) -> ResponseModel:
    data = await stock_provider_store.create_admin(obj)
    return response_base.success(data=data)


@router.put(
    '/providers/{provider_id}',
    summary='[Admin] 更新素材站（api_key 空串清空 / None 不改 / 非空加密覆盖）',
    dependencies=[Depends(RequestPermission('hasn_stock:provider:edit')), DependsRBAC],
)
async def update_stock_provider(obj: UpdateProviderParam, provider_id: Annotated[int, Path()]) -> ResponseModel:
    data = await stock_provider_store.update_admin(provider_id, obj)
    return response_base.success(data=data)


@router.delete(
    '/providers/{provider_id}',
    summary='[Admin] 删除素材站',
    dependencies=[Depends(RequestPermission('hasn_stock:provider:del')), DependsRBAC],
)
async def delete_stock_provider(provider_id: Annotated[int, Path()]) -> ResponseModel:
    ok = await stock_provider_store.delete_admin(provider_id)
    return response_base.success(data={'deleted': ok})
