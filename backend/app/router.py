from backend.app.marketplace.api.router import v1 as marketplace_v1, client as marketplace_client, publish as marketplace_publish
from backend.app.user_tier.api.router import v1 as user_tier_v1
from backend.app.projects.api.router import v1 as projects_v1
from backend.app.openclaw.api.router import v1 as openclaw_v1
from fastapi import APIRouter

from backend.app.admin.api.router import v1 as admin_v1, client as admin_client
from backend.app.llm.api.router import v1 as llm_v1
from backend.app.task.api.router import v1 as task_v1

router = APIRouter()

router.include_router(admin_v1)
router.include_router(task_v1)
router.include_router(llm_v1)
router.include_router(openclaw_v1)  # Openclaw Gateway API

router.include_router(projects_v1)
router.include_router(user_tier_v1)
router.include_router(marketplace_v1)
router.include_router(marketplace_client)  # 桌面端市场公开 API
router.include_router(marketplace_publish)  # 发布 API
router.include_router(admin_client)  # 桌面端版本检测公开 API
