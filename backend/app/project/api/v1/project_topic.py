"""项目私有选题 API"""

from __future__ import annotations

from typing import TYPE_CHECKING, Annotated

from fastapi import APIRouter, Path, Request

from backend.app.project.service.project_service import project_service
from backend.app.project.service.project_topic_service import ProjectTopicService
from backend.common.response.response_schema import ResponseSchemaModel
from backend.database.db import async_db_session

if TYPE_CHECKING:
    from collections.abc import Sequence

    from backend.app.project.schema.project_topic import ProjectTopicInfo, ProjectTopicSyncBatch

router = APIRouter()


@router.get("/{project_id}/topics", summary="获取项目私有选题列表")
async def get_project_topics(
    project_id: Annotated[int, Path(description="项目ID")],
) -> ResponseSchemaModel[Sequence[ProjectTopicInfo]]:
    data = await ProjectTopicService.list(project_id)
    return ResponseSchemaModel(data=data)


@router.post("/{project_id}/topics/sync", summary="同步项目私有选题（批量 upsert）")
async def sync_project_topics(
    request: Request,
    project_id: Annotated[int | str, Path(description="项目ID (支持整数ID或UUID)")],
    batch: ProjectTopicSyncBatch,
) -> ResponseSchemaModel[Sequence[ProjectTopicInfo]]:
    real_project_id = project_id
    if isinstance(project_id, str):
        if "-" in project_id:
            async with async_db_session() as db:
                project = await project_service.get_by_uuid(db=db, uuid=project_id)
                real_project_id = project.id
        elif project_id.isdigit():
            real_project_id = int(project_id)

    data = await ProjectTopicService.sync(
        request,
        project_id=int(real_project_id),
        batch=batch,
    )
    return ResponseSchemaModel(data=data)
