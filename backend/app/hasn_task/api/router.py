"""任务系统（AI-Native 应用 hasn_task，模块 12 设计 06）模块路由聚合。

- /api/v1/hasn-task/app/*    用户端（Owner JWT，owner 隔离）+ 任务同步 + run 摘要上报（Agent JWT）
- /api/v1/hasn-task/agent/*  Agent 端（Agent JWT，scope 闸 task:read/task:manage/task:run，M2 落地）

注：任务为私有用户内容，不开 open scope；admin 面沿用 app/hasn 既有管理端（M8 复核）。
"""

from fastapi import APIRouter

from backend.app.hasn_task.api.v1.agent.task import router as task_agent_router
from backend.app.hasn_task.api.v1.app.run import router as task_run_app_router
from backend.app.hasn_task.api.v1.app.sync import router as task_sync_app_router
from backend.app.hasn_task.api.v1.app.task import router as task_app_router
from backend.core.conf import settings

app = APIRouter(prefix=f'{settings.FASTAPI_API_V1_PATH}/hasn-task/app', tags=['任务系统-用户端'])
app.include_router(task_app_router)
app.include_router(task_run_app_router)
app.include_router(task_sync_app_router)

agent = APIRouter(prefix=f'{settings.FASTAPI_API_V1_PATH}/hasn-task/agent', tags=['任务系统-Agent端'])
agent.include_router(task_agent_router)
