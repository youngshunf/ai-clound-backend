"""项目私有选题服务层"""

from __future__ import annotations

from typing import TYPE_CHECKING

from backend.app.project.crud.crud_project_topic import project_topic_dao
from backend.database.db import async_db_session

if TYPE_CHECKING:
    from collections.abc import Sequence

    from fastapi import Request

    from backend.app.project.model.project_topic import ProjectTopic
    from backend.app.project.schema.project_topic import ProjectTopicSyncBatch


class ProjectTopicService:
    @staticmethod
    async def list(project_id: int) -> Sequence[ProjectTopic]:
        async with async_db_session() as db:
            stmt = await project_topic_dao.get_list_by_project(db, project_id)
            return await project_topic_dao.select_all(db, stmt)

    @staticmethod
    async def sync(
        request: Request,
        *,
        project_id: int,
        batch: ProjectTopicSyncBatch,
    ) -> Sequence[ProjectTopic]:
        async with async_db_session.begin() as db:
            topics = [t.model_dump() for t in batch.topics]
            return await project_topic_dao.sync_upsert_many(
                db,
                project_id=project_id,
                user_id=request.user.uuid,
                topics=topics,
            )
