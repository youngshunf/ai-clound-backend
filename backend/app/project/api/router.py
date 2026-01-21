"""项目管理 API 路由注册"""

from fastapi import APIRouter

from backend.app.project.api.v1.project import router as project_router
from backend.app.project.api.v1.platform_account import router as platform_account_router
from backend.app.project.api.v1.platform_account import user_accounts_router
from backend.core.conf import settings

v1 = APIRouter(prefix=settings.FASTAPI_API_V1_PATH)

# 项目管理
v1.include_router(project_router, prefix='/projects', tags=['项目管理'])
v1.include_router(platform_account_router, prefix='/projects', tags=['平台账号'])

# 用户级别的平台账号端点（用于同步）
v1.include_router(user_accounts_router, prefix='/platform-accounts', tags=['平台账号同步'])
