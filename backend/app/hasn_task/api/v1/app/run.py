"""任务执行记录 - 用户端 API（hasn_task 应用，canonical surface）

路径前缀: /api/v1/hasn-task/app
"""

from typing import Annotated

from fastapi import APIRouter, Path, Request

from backend.app.hasn_task.api.v1.app.task import owned_task
from backend.app.hasn_task.schema.run import GetHasnTaskRunDetail
from backend.app.hasn_task.service.run_service import hasn_task_run_service
from backend.common.pagination import DependsPagination, PageData
from backend.common.response.response_schema import ResponseModel, ResponseSchemaModel, response_base
from backend.common.security.jwt import DependsJwtAuth
from backend.database.db import CurrentSession

router = APIRouter()


@router.get(
    '/tasks/{task_id}/runs',
    summary='获取某任务的执行记录（倒序）',
    dependencies=[DependsJwtAuth, DependsPagination],
    name='hasn_task_app_list_runs',
)
async def list_task_runs(
    request: Request,
    db: CurrentSession,
    task_id: Annotated[int, Path(description='任务 ID')],
) -> ResponseSchemaModel[PageData[GetHasnTaskRunDetail]]:
    await owned_task(request, db, task_id)
    page_data = await hasn_task_run_service.get_list_by_task_id(db, task_id=task_id)
    return response_base.success(data=page_data)


@router.get(
    '/runs/{run_id}',
    summary='获取单条执行记录',
    dependencies=[DependsJwtAuth],
    name='hasn_task_app_get_run',
)
async def get_task_run(
    request: Request,
    db: CurrentSession,
    run_id: Annotated[int, Path(description='执行记录 ID')],
) -> ResponseModel:
    run = await hasn_task_run_service.get(db=db, pk=run_id)
    await owned_task(request, db, run.task_id)
    return response_base.success(data=GetHasnTaskRunDetail.model_validate(run))
