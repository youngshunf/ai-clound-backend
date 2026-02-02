from fastapi import APIRouter

from backend.app.admin.api.v1.auth import router as auth_router
from backend.app.admin.api.v1.log import router as log_router
from backend.app.admin.api.v1.monitor import router as monitor_router
from backend.app.admin.api.v1.sys import router as sys_router
from backend.app.admin.api.v1.test_user import router as test_user_router
from backend.app.admin.api.v1.app_version import router as app_version_router
from backend.app.admin.api.v1.client_version import router as client_version_router
from backend.core.conf import settings

# 管理端 API（需要认证）
v1 = APIRouter(prefix=settings.FASTAPI_API_V1_PATH)

v1.include_router(app_version_router, prefix='/app/versions', tags=['应用版本管理'])
v1.include_router(test_user_router, prefix='/test/users')
v1.include_router(auth_router)
v1.include_router(sys_router)
v1.include_router(log_router)
v1.include_router(monitor_router)

# 桌面端公开 API（不需要认证）
# 注册到 /api/v1/client/version 路径下
client = APIRouter(prefix=f'{settings.FASTAPI_API_V1_PATH}/client', tags=['桌面端-版本检测'])
client.include_router(client_version_router, prefix='/version')
