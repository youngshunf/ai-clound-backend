from fastapi import APIRouter

from backend.app.marketplace.api.v1.marketplace_category import router as marketplace_category_router
from backend.app.marketplace.api.v1.marketplace_skill import router as marketplace_skill_router
from backend.app.marketplace.api.v1.marketplace_skill_version import router as marketplace_skill_version_router
from backend.app.marketplace.api.v1.marketplace_app import router as marketplace_app_router
from backend.app.marketplace.api.v1.marketplace_app_version import router as marketplace_app_version_router
from backend.app.marketplace.api.v1.marketplace_download import router as marketplace_download_router
from backend.app.marketplace.api.v1.download import router as download_router
from backend.app.marketplace.api.v1.sync import router as sync_router
from backend.app.marketplace.api.v1.search import router as search_router
from backend.core.conf import settings

v1 = APIRouter(prefix=f'{settings.FASTAPI_API_V1_PATH}/marketplace', tags=['技能市场'])

v1.include_router(marketplace_category_router, prefix='/categories')
v1.include_router(marketplace_skill_version_router, prefix='/skills/versions')  # 更具体的路由放前面
v1.include_router(marketplace_skill_router, prefix='/skills')
v1.include_router(marketplace_app_version_router, prefix='/apps/versions')  # 更具体的路由放前面
v1.include_router(marketplace_app_router, prefix='/apps')
v1.include_router(download_router, prefix='/download')  # 下载 API
v1.include_router(sync_router, prefix='/sync')  # 同步 API
v1.include_router(search_router, prefix='/search')  # 搜索 API
v1.include_router(marketplace_download_router, prefix='/downloads')  # 下载记录管理
