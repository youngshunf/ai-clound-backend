from fastapi import APIRouter

from backend.app.project.api.v1.project import router as project_router

router = APIRouter()

router.include_router(project_router, prefix='/projects', tags=['项目管理'])
