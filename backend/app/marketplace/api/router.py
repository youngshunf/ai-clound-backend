from fastapi import APIRouter

from backend.app.marketplace.api.v1.admin.marketplace_category import router as admin_marketplace_category_router
from backend.app.marketplace.api.v1.admin.marketplace_skill import router as admin_marketplace_skill_router
from backend.app.marketplace.api.v1.admin.marketplace_skill_version import (
    router as admin_marketplace_skill_version_router,
)
from backend.app.marketplace.api.v1.admin.marketplace_sync_log import router as admin_marketplace_sync_log_router
from backend.app.marketplace.api.v1.admin.marketplace_template import router as admin_marketplace_template_router
from backend.app.marketplace.api.v1.admin.marketplace_template_version import (
    router as admin_marketplace_template_version_router,
)
from backend.app.marketplace.api.v1.admin.review import router as admin_review_router
from backend.app.marketplace.api.v1.admin.sync_management import router as admin_sync_management_router
from backend.app.marketplace.api.v1.agent.marketplace_personal_skill import (
    router as agent_marketplace_personal_skill_router,
)
from backend.app.marketplace.api.v1.app.marketplace_personal_skill import (
    router as app_marketplace_personal_skill_router,
)
from backend.app.marketplace.api.v1.app.marketplace_skills import router as app_marketplace_skills_router
from backend.app.marketplace.api.v1.app.marketplace_template import router as app_marketplace_template_router
from backend.app.marketplace.api.v1.marketplace_download import router as marketplace_download_router
from backend.app.marketplace.api.v1.open.browse import router as open_browse_router
from backend.app.marketplace.api.v1.open.marketplace_skills import router as open_marketplace_skills_router
from backend.app.marketplace.api.v1.open.marketplace_template import router as open_marketplace_template_router
from backend.app.marketplace.api.v1.publish import router as publish_router  # 发布 API
from backend.app.marketplace.api.v1.skill_pack import router as skill_pack_router  # 技能包（skill_pack）
from backend.app.marketplace.api.v1.webhook import router as webhook_router  # GitHub Webhook
from backend.core.conf import settings

# 发布 API（需要 API Key 认证）
# 注册到 /api/v1/marketplace/publish 路径下
publish = APIRouter(prefix=f'{settings.FASTAPI_API_V1_PATH}/marketplace/publish', tags=['市场-发布'])
publish.include_router(publish_router)


# --- 用户端（仅 JWT） ---
app = APIRouter(prefix=f'{settings.FASTAPI_API_V1_PATH}/marketplace/app', tags=['技能市场同步日志'])

app.include_router(app_marketplace_skills_router, prefix='/skills', tags=['技能市场-用户技能'])
app.include_router(app_marketplace_template_router, prefix='/templates', tags=['技能市场-用户模板'])
# 技能包 skill_pack（实施/91 B2.1）：先只挂 app scope（Owner JWT）。open 公开浏览延后到 B5
# （需先补 status/visibility 过滤防泄露，B0 同款教训）。路径 /api/v1/marketplace/app/skill-packs。
app.include_router(skill_pack_router, prefix='/skill-packs', tags=['技能市场-技能包'])
# 个人技能库（SKILLSYNC-C2）：owner 维度个人技能管理（含自动同步 upsert / 目录上传 / 一键发布 / 装配分身）。
app.include_router(app_marketplace_personal_skill_router, prefix='/personal-skills', tags=['技能市场-个人技能库'])

# --- Agent（Agent JWT） ---
# 个人技能私有包带鉴权下载（供 hermes provisioning 跨设备物化，难点5）。
agent = APIRouter(prefix=f'{settings.FASTAPI_API_V1_PATH}/marketplace/agent', tags=['技能市场-Agent'])
agent.include_router(
    agent_marketplace_personal_skill_router, prefix='/personal-skills', tags=['技能市场-个人技能库']
)

# --- 公开（无需认证） ---
open_api = APIRouter(prefix=f'{settings.FASTAPI_API_V1_PATH}/marketplace/open', tags=['技能市场'])

open_api.include_router(open_browse_router, tags=['技能市场-公开浏览'])
open_api.include_router(open_marketplace_skills_router, prefix='/skills', tags=['技能市场-公开API'])
open_api.include_router(open_marketplace_template_router, prefix='/templates', tags=['技能市场-公开模板'])

admin = APIRouter(prefix=f'{settings.FASTAPI_API_V1_PATH}/marketplace/admin', tags=['技能市场-审核'])
admin.include_router(admin_review_router)
admin.include_router(admin_marketplace_skill_version_router, prefix='/skills/versions', tags=['技能版本-技能版本'])
admin.include_router(admin_marketplace_skill_router, prefix='/skills', tags=['技能市场技能-技能市场技能'])
admin.include_router(
    admin_marketplace_template_version_router,
    prefix='/templates/versions',
    tags=['技能市场模板版本-模板版本'],
)
admin.include_router(admin_marketplace_template_router, prefix='/templates', tags=['技能市场模板-模板'])
admin.include_router(admin_marketplace_category_router, prefix='/categories', tags=['技能市场分类-技能市场分类'])
admin.include_router(admin_marketplace_sync_log_router, prefix='/sync/logs', tags=['技能市场同步日志-技能市场同步日志'])
admin.include_router(admin_sync_management_router, prefix='/sync', tags=['技能市场-同步管理'])
admin.include_router(marketplace_download_router, prefix='/downloads', tags=['技能市场-下载记录管理'])

# --- Webhook（GitHub 签名） ---
webhook = APIRouter(prefix=f'{settings.FASTAPI_API_V1_PATH}/marketplace/webhook', tags=['技能市场-GitHub Webhook'])
webhook.include_router(webhook_router)
