"""任务定义 - 用户端 API（hasn_task 应用，canonical surface）

认证方式: DependsJwtAuth（仅当前登录用户）
数据隔离: owner_hasn_id（request.user.hasn_id / hasn_humans 映射）
路径前缀: /api/v1/hasn-task/app

旧 /api/v1/hasn/app/hasn/tasks* 与 /api/v1/hasn/tasks*（app/hasn 内）为兼容遗留面，
daemon/webui 全量切到本面后于 M8 删除（设计 06 §11）。
"""

from typing import Annotated

from fastapi import APIRouter, Path, Request

from backend.app.hasn.crud.crud_hasn_humans import hasn_humans_dao
from backend.app.hasn_task.model.task import HasnTask
from backend.app.hasn_task.schema.task import (
    CreateHasnTaskParam,
    DeleteHasnTaskParam,
    GetHasnTaskDetail,
    UpdateHasnTaskParam,
)
from backend.app.hasn_task.service.task_service import hasn_task_service
from backend.common.exception import errors
from backend.common.pagination import DependsPagination, PageData
from backend.common.response.response_schema import ResponseModel, ResponseSchemaModel, response_base
from backend.common.security.jwt import DependsJwtAuth
from backend.database.db import CurrentSession, CurrentSessionTransaction

router = APIRouter()


async def current_owner_id(request: Request, db: CurrentSession) -> str:
    """解析当前登录用户的 owner hasn_id（无 HASN 身份 → 403）。"""
    owner_id = getattr(request.user, 'hasn_id', None)
    if owner_id:
        return owner_id
    hasn_human = await hasn_humans_dao.get_by_user_id(db, user_id=request.user.id)
    if not hasn_human:
        raise errors.ForbiddenError(msg='当前用户未注册 HASN 身份')
    return hasn_human.hasn_id


async def owned_task(request: Request, db: CurrentSession, task_id: int) -> HasnTask:
    """取任务并校验归属；跨户 → NotFound（不泄露存在性）。"""
    owner_id = await current_owner_id(request, db)
    task = await hasn_task_service.get(db=db, pk=task_id)
    if task.owner_id != owner_id:
        raise errors.NotFoundError(msg='任务定义不存在')
    return task


@router.get(
    '/tasks',
    summary='获取我的任务列表',
    dependencies=[DependsJwtAuth, DependsPagination],
    name='hasn_task_app_list',
)
async def list_my_tasks(
    request: Request,
    db: CurrentSession,
) -> ResponseSchemaModel[PageData[GetHasnTaskDetail]]:
    owner_id = await current_owner_id(request, db)
    page_data = await hasn_task_service.get_list_by_owner(db=db, owner_id=owner_id)
    return response_base.success(data=page_data)


@router.post(
    '/tasks',
    summary='创建任务（自动计算 next_run_at）',
    dependencies=[DependsJwtAuth],
    name='hasn_task_app_create',
)
async def create_my_task(
    request: Request,
    db: CurrentSessionTransaction,
    obj: CreateHasnTaskParam,
) -> ResponseModel:
    owner_id = await current_owner_id(request, db)
    obj.owner_id = owner_id
    task = await hasn_task_service.create_with_schedule(db=db, obj=obj)
    return response_base.success(data={'task_id': task.id})


@router.get(
    '/tasks/{task_id}',
    summary='获取我的任务详情',
    dependencies=[DependsJwtAuth],
    name='hasn_task_app_detail',
)
async def get_my_task(
    request: Request,
    db: CurrentSession,
    task_id: Annotated[int, Path(description='任务 ID')],
) -> ResponseModel:
    task = await owned_task(request, db, task_id)
    return response_base.success(data=GetHasnTaskDetail.model_validate(task))


@router.put(
    '/tasks/{task_id}',
    summary='更新我的任务',
    dependencies=[DependsJwtAuth],
    name='hasn_task_app_update',
)
async def update_my_task(
    request: Request,
    db: CurrentSessionTransaction,
    task_id: Annotated[int, Path(description='任务 ID')],
    obj: UpdateHasnTaskParam,
) -> ResponseModel:
    task = await owned_task(request, db, task_id)
    obj.owner_id = task.owner_id
    count = await hasn_task_service.update(db=db, pk=task_id, obj=obj)
    return response_base.success(data={'updated': count})


@router.delete(
    '/tasks/{task_id}',
    summary='删除我的任务',
    dependencies=[DependsJwtAuth],
    name='hasn_task_app_delete',
)
async def delete_my_task(
    request: Request,
    db: CurrentSessionTransaction,
    task_id: Annotated[int, Path(description='任务 ID')],
) -> ResponseModel:
    await owned_task(request, db, task_id)
    count = await hasn_task_service.delete(db=db, obj=DeleteHasnTaskParam(pks=[task_id]))
    return response_base.success(data={'deleted': count})


@router.post(
    '/tasks/{task_id}/enable',
    summary='启用我的任务',
    dependencies=[DependsJwtAuth],
    name='hasn_task_app_enable',
)
async def enable_my_task(
    request: Request,
    db: CurrentSessionTransaction,
    task_id: Annotated[int, Path(description='任务 ID')],
) -> ResponseModel:
    await owned_task(request, db, task_id)
    task = await hasn_task_service.enable_task(db=db, task_id=task_id)
    return response_base.success(data={'task_id': task.id, 'enabled': task.enabled})


@router.post(
    '/tasks/{task_id}/disable',
    summary='禁用我的任务',
    dependencies=[DependsJwtAuth],
    name='hasn_task_app_disable',
)
async def disable_my_task(
    request: Request,
    db: CurrentSessionTransaction,
    task_id: Annotated[int, Path(description='任务 ID')],
) -> ResponseModel:
    await owned_task(request, db, task_id)
    task = await hasn_task_service.disable_task(db=db, task_id=task_id)
    return response_base.success(data={'task_id': task.id, 'enabled': task.enabled})
