from collections.abc import Sequence
from uuid import UUID

from sqlalchemy import Select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy_crud_plus import CRUDPlus

from backend.app.hasn_copilot.model import MeetingEnhancementRevisions
from backend.app.hasn_copilot.schema.meeting_enhancement_revisions import (
    CreateMeetingEnhancementRevisionsParam,
    UpdateMeetingEnhancementRevisionsParam,
)


class CRUDMeetingEnhancementRevisions(CRUDPlus[MeetingEnhancementRevisions]):
    async def get(self, db: AsyncSession, pk: UUID) -> MeetingEnhancementRevisions | None:
        """
        获取会议会后增强候选 revision（云端权威，含淘汰审计元数据）

        :param db: 数据库会话
        :param pk: 会议会后增强候选 revision（云端权威，含淘汰审计元数据） ID
        :return:
        """
        return await self.select_model(db, pk)

    async def get_select(self) -> Select:
        """获取会议会后增强候选 revision（云端权威，含淘汰审计元数据）列表查询表达式"""
        return await self.select_order('id', 'desc')

    async def get_all(self, db: AsyncSession) -> Sequence[MeetingEnhancementRevisions]:
        """
        获取所有会议会后增强候选 revision（云端权威，含淘汰审计元数据）

        :param db: 数据库会话
        :return:
        """
        return await self.select_models(db)

    async def create(self, db: AsyncSession, obj: CreateMeetingEnhancementRevisionsParam) -> None:
        """
        创建会议会后增强候选 revision（云端权威，含淘汰审计元数据）

        :param db: 数据库会话
        :param obj: 创建会议会后增强候选 revision（云端权威，含淘汰审计元数据）参数
        :return:
        """
        await self.create_model(db, obj)

    async def update(self, db: AsyncSession, pk: UUID, obj: UpdateMeetingEnhancementRevisionsParam) -> int:
        """
        更新会议会后增强候选 revision（云端权威，含淘汰审计元数据）

        :param db: 数据库会话
        :param pk: 会议会后增强候选 revision（云端权威，含淘汰审计元数据） ID
        :param obj: 更新 会议会后增强候选 revision（云端权威，含淘汰审计元数据）参数
        :return:
        """
        return await self.update_model(db, pk, obj)

    async def delete(self, db: AsyncSession, pks: list[UUID]) -> int:
        """
        批量删除会议会后增强候选 revision（云端权威，含淘汰审计元数据）

        :param db: 数据库会话
        :param pks: 会议会后增强候选 revision（云端权威，含淘汰审计元数据） ID 列表
        :return:
        """
        return await self.delete_model_by_column(db, allow_multiple=True, id__in=pks)


meeting_enhancement_revisions_dao: CRUDMeetingEnhancementRevisions = CRUDMeetingEnhancementRevisions(
    MeetingEnhancementRevisions
)
