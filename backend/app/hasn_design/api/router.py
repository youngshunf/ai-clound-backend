from fastapi import APIRouter

# --- 管理端（JWT + RBAC） ---
from backend.app.hasn_design.api.v1.admin.hasn_design_project import router as admin_hasn_design_project_router

# --- 用户端（仅 JWT） ---
from backend.app.hasn_design.api.v1.app.design_share import router as app_design_share_router
from backend.core.conf import settings

# ========================================
# 管理端 API（JWT + RBAC）
# 路径前缀: /api/v1/hasn_design/
# ========================================
v1 = APIRouter(
    prefix=f'{settings.FASTAPI_API_V1_PATH}/hasn_design',
    tags=['设计项目（矢量设计 design：云端轻登记元数据，源文件本地优先）管理'],
)

v1.include_router(
    admin_hasn_design_project_router,
    prefix='/hasn-design-project',
    tags=['矢量设计-设计项目（管理）'],
)

# ========================================
# 用户端 API（仅 JWT，无 RBAC）
# 路径前缀: /api/v1/hasn_design/app/
# ========================================
app = APIRouter(
    prefix=f'{settings.FASTAPI_API_V1_PATH}/hasn_design/app',
    tags=['设计项目（矢量设计 design：云端轻登记元数据，源文件本地优先）用户端'],
)

# 分享协作（项目；全复用泛型 resource_share）——路径 /api/v1/hasn_design/app/projects/{project_id}/shares
app.include_router(
    app_design_share_router,
    tags=['矢量设计-设计项目分享（用户端）'],
)

# ========================================
# 公开 API（无需认证）
# 路径前缀: /api/v1/hasn_design/open/
# ========================================
open_api = APIRouter(
    prefix=f'{settings.FASTAPI_API_V1_PATH}/hasn_design/open',
    tags=['设计项目（矢量设计 design：云端轻登记元数据，源文件本地优先）公开'],
)


# ========================================
# Agent API
# 路径前缀: /api/v1/hasn_design/agent/
# ========================================
agent = APIRouter(
    prefix=f'{settings.FASTAPI_API_V1_PATH}/hasn_design/agent',
    tags=['设计项目（矢量设计 design：云端轻登记元数据，源文件本地优先）Agent'],
)
