"""项目私有选题数据库操作类"""

from __future__ import annotations

from typing import TYPE_CHECKING

from sqlalchemy import Select, select
from sqlalchemy_crud_plus import CRUDPlus

from backend.app.project.model.project_topic import ProjectTopic
from backend.utils.timezone import timezone

if TYPE_CHECKING:
    from collections.abc import Sequence

    from sqlalchemy.ext.asyncio import AsyncSession


class CRUDProjectTopic(CRUDPlus[ProjectTopic]):
    """项目私有选题数据库操作类"""

    async def select_all(self, db: AsyncSession, stmt: Select) -> Sequence[ProjectTopic]:
        result = await db.execute(stmt)
        return result.scalars().all()

    async def get_list_by_project(self, db: AsyncSession, project_id: int) -> Select:
        return await self.select_order(
            "created_time",
            "desc",
            project_id=project_id,
            is_deleted=False,
        )

    async def sync_upsert_many(
        self,
        db: AsyncSession,
        *,
        project_id: int,
        user_id: int,
        topics: Sequence[dict],
    ) -> list[ProjectTopic]:
        saved: list[ProjectTopic] = []
        now = timezone.now()

        for data in topics:
            topic_id = data.get("id")
            source_uid = data.get("source_uid")

            stmt = None
            if topic_id:
                stmt = select(self.model).where(
                    self.model.project_id == project_id,
                    self.model.id == topic_id,
                )
            elif source_uid:
                stmt = select(self.model).where(
                    self.model.project_id == project_id,
                    self.model.source_uid == source_uid,
                )

            existing = None
            if stmt is not None:
                result = await db.execute(stmt)
                existing = result.scalars().first()

            if existing:
                update_data = data.copy()
                for k in [
                    "project_id",
                    "user_id",
                    "created_time",
                    "updated_time",
                ]:
                    update_data.pop(k, None)

                update_data["user_id"] = user_id
                update_data["server_version"] = existing.server_version + 1
                update_data["last_sync_at"] = now

                if existing.is_deleted and not data.get("is_deleted"):
                    update_data["is_deleted"] = False
                    update_data["deleted_at"] = None

                for k, v in update_data.items():
                    setattr(existing, k, v)

                await db.flush()
                await db.refresh(existing)
                saved.append(existing)
            else:
                create_data = data.copy()
                create_data["project_id"] = project_id
                create_data["user_id"] = user_id
                create_data["server_version"] = 1
                create_data["last_sync_at"] = now

                new_topic = self.model(**create_data)
                db.add(new_topic)
                await db.flush()
                await db.refresh(new_topic)
                saved.append(new_topic)

        return saved


project_topic_dao: CRUDProjectTopic = CRUDProjectTopic(ProjectTopic)
