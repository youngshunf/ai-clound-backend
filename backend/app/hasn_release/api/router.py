"""hasn_release 应用路由聚合（桌面端发布与自动更新）。

三套面（无 agent/app 裸 CRUD——发布是运维动作，分身不经此写库）。
路由前缀按项目约定去 `hasn_` 前缀（同 `hasn_community`→`/api/v1/community/*`）：
  - admin  管理端（JWT + RBAC）：手动上传发布 / GitHub 构建 / 版本管理  → /api/v1/release/admin
  - open   公开（无认证）：官网 Hero/下载页 latest、releases、Tauri updater、下载重定向 → /api/v1/release/open
  - ci     CI 回调（Bearer CI 密钥）：GitHub Actions 出包回调落库 → /api/v1/release/ci
"""

from fastapi import APIRouter

from backend.app.hasn_release.api.v1.admin.release import router as admin_release_router
from backend.app.hasn_release.api.v1.ci.release import router as ci_release_router
from backend.app.hasn_release.api.v1.open.release import router as open_release_router
from backend.core.conf import settings

_BASE = f'{settings.FASTAPI_API_V1_PATH}/release'

admin = APIRouter(prefix=f'{_BASE}/admin', tags=['桌面端发布-管理端'])
admin.include_router(admin_release_router)

open_api = APIRouter(prefix=f'{_BASE}/open', tags=['桌面端发布-公开'])
open_api.include_router(open_release_router)

ci = APIRouter(prefix=f'{_BASE}/ci', tags=['桌面端发布-CI回调'])
ci.include_router(ci_release_router)
