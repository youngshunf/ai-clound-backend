"""任务定义 - 用户端 API（hasn_task 应用，canonical surface）

认证方式: DependsJwtAuth（仅当前登录用户）
数据隔离: owner_hasn_id（request.user.hasn_id / hasn_humans 映射）
路径前缀: /api/v1/hasn-task/app

旧 /api/v1/hasn/app/hasn/tasks* 与 /api/v1/hasn/tasks*（app/hasn 内）为兼容遗留面，
daemon/webui 全量切到本面后于 M8 删除（设计 06 §11）。
"""

from typing import Annotated

from fastapi import APIRouter, Path, Request

from backend.app.hasn_core import identity
from backend.app.hasn_task.model.task import HasnTask
from backend.app.hasn_task.schema.task import (
    CreateHasnTaskParam,
    DeleteHasnTaskParam,
    GetHasnTaskDetail,
    UpdateHasnTaskParam,
)
from backend.app.hasn_task.service.builtin_seeding_service import (
    builtin_update_available,
    load_builtin_catalog_map,
    update_builtin_task_from_catalog,
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
    hasn_human = await identity.get_human_by_user_id(db, user_id=request.user.id)
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
    # 读时派生「可更新」：join catalog 比对 builtin_synced_revision（不落库，§6.6）。
    # 注意：paging_data 返回的是 dict（model_dump），items 是 dict 键——必须用 page_data['items']；
    # 误写 page_data.items 会拿到内建方法 dict.items（不可迭代）→ 500，daemon 吞 500 后
    # builtin_update_available 静默恒 false（«可更新» 功能整体失效）。详见 builtin task lifecycle E2E。
    cat_map = await load_builtin_catalog_map(db)
    items = []
    for row in page_data['items']:
        detail = GetHasnTaskDetail.model_validate(row)
        if detail.builtin_key:
            detail.builtin_update_available = builtin_update_available(
                cat_map.get(detail.builtin_key), detail.builtin_synced_revision
            )
        items.append(detail)
    page_data['items'] = items
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
    detail = GetHasnTaskDetail.model_validate(task)
    if detail.builtin_key:
        cat_map = await load_builtin_catalog_map(db)
        detail.builtin_update_available = builtin_update_available(
            cat_map.get(detail.builtin_key), detail.builtin_synced_revision
        )
    return response_base.success(data=detail)


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
    task = await owned_task(request, db, task_id)
    # 内置任务可改不可删（设计 §6.3）：只能停用，不能删除。
    if task.created_by_kind == 'builtin':
        raise errors.ForbiddenError(msg='内置任务不可删除，只能停用')
    count = await hasn_task_service.delete(db=db, obj=DeleteHasnTaskParam(pks=[task_id]))
    return response_base.success(data={'deleted': count})


@router.post(
    '/tasks/{task_id}/refresh-builtin',
    summary='把内置任务更新到官方最新版（§6.6 用户手动决策，绝不自动调用）',
    dependencies=[DependsJwtAuth],
    name='hasn_task_app_refresh_builtin',
)
async def refresh_my_builtin_task(
    request: Request,
    db: CurrentSessionTransaction,
    task_id: Annotated[int, Path(description='任务 ID')],
) -> ResponseModel:
    """应用官方目录的最新定义（保留用户 enabled/agent_id），追平 builtin_synced_revision。"""
    task = await owned_task(request, db, task_id)
    if not task.task_uuid:
        raise errors.RequestError(msg='任务缺少端云稳定 ID，无法更新')
    updated = await update_builtin_task_from_catalog(db, owner_id=task.owner_id, task_uuid=task.task_uuid)
    return response_base.success(
        data={'task_id': updated.id, 'builtin_synced_revision': updated.builtin_synced_revision}
    )


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


@router.post(
    '/tasks/{task_id}/approve',
    summary='同意 agent 建的周期任务（pending_approval → scheduled，D4 业务态）',
    dependencies=[DependsJwtAuth],
    name='hasn_task_app_approve',
)
async def approve_my_task(
    request: Request,
    db: CurrentSessionTransaction,
    task_id: Annotated[int, Path(description='任务 ID')],
) -> ResponseModel:
    from backend.app.hasn_task.service.agent_task_service import agent_task_service

    task = await owned_task(request, db, task_id)
    if not task.task_uuid:
        raise errors.RequestError(msg='任务缺少端云稳定 ID，无法审批')
    data = await agent_task_service.approve_task(db, owner_id=task.owner_id, task_uuid=task.task_uuid)
    return response_base.success(data={'task': data})


@router.post(
    '/tasks/{task_id}/reject',
    summary='拒绝 agent 建的周期任务（pending_approval → rejected，软删可见）',
    dependencies=[DependsJwtAuth],
    name='hasn_task_app_reject',
)
async def reject_my_task(
    request: Request,
    db: CurrentSessionTransaction,
    task_id: Annotated[int, Path(description='任务 ID')],
) -> ResponseModel:
    from backend.app.hasn_task.service.agent_task_service import agent_task_service

    task = await owned_task(request, db, task_id)
    if not task.task_uuid:
        raise errors.RequestError(msg='任务缺少端云稳定 ID，无法审批')
    data = await agent_task_service.reject_task(db, owner_id=task.owner_id, task_uuid=task.task_uuid)
    return response_base.success(data={'task': data})
