"""管理端 — new-api 用户 Token/额度/用量 API

认证: JWT + RBAC
用途: 管理员查看所有用户的 API Token、额度、用量，以及修改用户额度
"""

import time
from typing import Annotated

from fastapi import APIRouter, Depends, Path, Query

from backend.app.billing.schema.newapi_quota import (
    AdminNewApiUserList,
    AdminQuotaInfo,
    AdminQuotaUpdateParam,
    AdminUsageDetail,
    AdminUsageSummary,
)
from backend.app.billing.service.newapi_quota_service import newapi_quota_service
from backend.common.response.response_schema import ResponseModel, ResponseSchemaModel, response_base
from backend.common.security.jwt import DependsJwtAuth
from backend.common.security.permission import RequestPermission
from backend.common.security.rbac import DependsRBAC
from backend.database.db import CurrentSession

router = APIRouter()


@router.get(
    '',
    summary='分页查询所有用户的 Token/额度概览',
    dependencies=[DependsJwtAuth],
 name='admin_admin_newapi_get_all_users')
async def admin_newapi_get_all_users(
    db: CurrentSession,
    page: Annotated[int, Query(ge=1, description='页码')] = 1,
    size: Annotated[int, Query(ge=1, le=200, description='每页条数')] = 20,
    user_keyword: Annotated[str | None, Query(description='用户昵称/手机号搜索')] = None,
    app_code: Annotated[str | None, Query(description='应用标识筛选')] = None,
    mapping_status: Annotated[str | None, Query(description='映射状态筛选')] = None,
) -> ResponseSchemaModel[AdminNewApiUserList]:
    data = await newapi_quota_service.get_user_list(
        db,
        page=page,
        size=size,
        user_keyword=user_keyword,
        app_code=app_code,
        mapping_status=mapping_status,
    )
    return response_base.success(data=data)


@router.get(
    '/{user_id}/quota',
    summary='查询指定用户的详细额度信息',
    dependencies=[DependsJwtAuth],
 name='admin_admin_newapi_get_user_quota')
async def admin_newapi_get_user_quota(
    db: CurrentSession,
    user_id: Annotated[int, Path(description='唤星用户 ID')],
    app_code: Annotated[str, Query(description='应用标识')] = 'huanxing',
) -> ResponseSchemaModel[AdminQuotaInfo]:
    data = await newapi_quota_service.get_user_quota(db, user_id, app_code=app_code)
    return response_base.success(data=data)


@router.put(
    '/{user_id}/quota',
    summary='修改用户额度',
    dependencies=[
        Depends(RequestPermission('user:newapi:quota:edit')),
        DependsRBAC,
    ],
)
async def admin_newapi_update_user_quota(
    db: CurrentSession,
    user_id: Annotated[int, Path(description='唤星用户 ID')],
    obj: AdminQuotaUpdateParam,
    app_code: Annotated[str, Query(description='应用标识')] = 'huanxing',
) -> ResponseModel:
    await newapi_quota_service.update_user_quota(db, user_id, obj.new_quota, app_code=app_code)
    return response_base.success()


@router.get(
    '/{user_id}/usage/summary',
    summary='查询指定用户的用量统计（按模型分组）',
    dependencies=[DependsJwtAuth],
 name='admin_admin_newapi_get_usage_summary')
async def admin_newapi_get_usage_summary(
    db: CurrentSession,
    user_id: Annotated[int, Path(description='唤星用户 ID')],
    start_time: Annotated[int | None, Query(description='开始时间 (unix timestamp)')] = None,
    end_time: Annotated[int | None, Query(description='结束时间 (unix timestamp)')] = None,
    app_code: Annotated[str, Query(description='应用标识')] = 'huanxing',
) -> ResponseSchemaModel[AdminUsageSummary]:
    now = int(time.time())
    if not start_time:
        start_time = now - 30 * 86400
    if not end_time:
        end_time = now
    data = await newapi_quota_service.get_usage_summary(
        db, user_id, start_time, end_time, app_code=app_code,
    )
    return response_base.success(data=data)


@router.get(
    '/{user_id}/usage/detail',
    summary='查询指定用户的用量明细（分页）',
    dependencies=[DependsJwtAuth],
 name='admin_admin_newapi_get_usage_detail')
async def admin_newapi_get_usage_detail(
    db: CurrentSession,
    user_id: Annotated[int, Path(description='唤星用户 ID')],
    start_time: Annotated[int | None, Query(description='开始时间 (unix timestamp)')] = None,
    end_time: Annotated[int | None, Query(description='结束时间 (unix timestamp)')] = None,
    model_name: Annotated[str | None, Query(description='模型名称筛选')] = None,
    limit: Annotated[int, Query(ge=1, le=200, description='每页条数')] = 50,
    offset: Annotated[int, Query(ge=0, description='偏移量')] = 0,
    app_code: Annotated[str, Query(description='应用标识')] = 'huanxing',
) -> ResponseSchemaModel[AdminUsageDetail]:
    now = int(time.time())
    if not start_time:
        start_time = now - 30 * 86400
    if not end_time:
        end_time = now
    data = await newapi_quota_service.get_usage_detail(
        db, user_id, start_time, end_time,
        model_name=model_name, limit=limit, offset=offset, app_code=app_code,
    )
    return response_base.success(data=data)
