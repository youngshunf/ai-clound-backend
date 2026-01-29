from backend.app.marketplace.api.router import v1 as marketplace_v1
from backend.app.user_tier.api.router import v1 as user_tier_v1
from backend.app.projects.api.router import v1 as projects_v1
from fastapi import APIRouter

from backend.app.admin.api.router import v1 as admin_v1
from backend.app.llm.api.router import v1 as llm_v1
from backend.app.task.api.router import v1 as task_v1

router = APIRouter()

router.include_router(admin_v1)
router.include_router(task_v1)
router.include_router(llm_v1)

router.include_router(projects_v1)
router.include_router(user_tier_v1)
router.include_router(marketplace_v1)
