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
from backend.app.marketplace.api.v1.client import router as client_router  # 桌面端公开 API
from backend.app.marketplace.api.v1.publish import router as publish_router  # 发布 API
from backend.core.conf import settings

# 管理端 API（需要认证）
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

# 桌面端公开 API（不需要认证）
# 注册到 /api/v1/marketplace/client 路径下
client = APIRouter(prefix=f'{settings.FASTAPI_API_V1_PATH}/marketplace/client', tags=['市场-桌面端'])
client.include_router(client_router)

# 发布 API（需要 API Key 认证）
# 注册到 /api/v1/marketplace/publish 路径下
publish = APIRouter(prefix=f'{settings.FASTAPI_API_V1_PATH}/marketplace/publish', tags=['市场-发布'])
publish.include_router(publish_router)
