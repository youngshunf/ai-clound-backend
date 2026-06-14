"""workbench 模块路由注册（工作台简报/偏好/内置任务目录）。

本模块由 app/hasn 的工作台子域按 ADR-15 §4 抽出为独立 AI-Native 应用 workbench。
**URL 前缀保持 `/api/v1/hasn/app/workbench/*` 不变**（daemon
`modules/huanxing/workbench.rs`→`domains/workbench/cloud.rs::WorkbenchCloud` 代理路径逐字依赖它）：
原先工作台 API 挂在 app/hasn 的 `app` 路由器（prefix `/api/v1/hasn/app`）下，抽出后这里
重建同名同前缀的承载路由器，端点路径（`/workbench/...`）原样不动。
"""

from fastapi import APIRouter

from backend.core.conf import settings

from backend.app.workbench.api.v1.app.workbench import router as workbench_router

# 用户端（Owner JWT），保持原 URL 前缀 /api/v1/hasn/app（端点内含 /workbench/*）
workbench_app = APIRouter(prefix=f'{settings.FASTAPI_API_V1_PATH}/hasn/app', tags=['工作台'])
workbench_app.include_router(workbench_router, tags=['工作台'])
