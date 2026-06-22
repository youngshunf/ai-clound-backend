"""home 模块路由注册（应用目录/启动器 + 工作台首页：简报/偏好/内置任务目录）。

本模块由 app/hasn 的工作台子域按 ADR-15 §4 抽出。端点路径已统一到 `/apps/*`（应用目录/启动器/
权益）与 `/home/*`（首页偏好/简报/内置任务），URL 前缀仍为 `/api/v1/hasn/app`：
原先工作台 API 挂在 app/hasn 的 `app` 路由器（prefix `/api/v1/hasn/app`）下，抽出后这里
重建同前缀的承载路由器，端点路径走 `/apps/...` 与 `/home/...`。
"""

from fastapi import APIRouter

from backend.app.home.api.v1.app.home import router as home_router
from backend.core.conf import settings

# 用户端（Owner JWT），保持原 URL 前缀 /api/v1/hasn/app（端点内含 /apps/* 与 /home/*）
home_app = APIRouter(prefix=f'{settings.FASTAPI_API_V1_PATH}/hasn/app', tags=['应用/首页'])
home_app.include_router(home_router, tags=['应用/首页'])
