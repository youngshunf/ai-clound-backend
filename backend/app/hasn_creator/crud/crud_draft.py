from typing import Sequence

from sqlalchemy import Select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy_crud_plus import CRUDPlus

from backend.app.hasn_creator.model import Draft
from backend.app.hasn_creator.schema.draft import CreateDraftParam, UpdateDraftParam


class CRUDDraft(CRUDPlus[Draft]):
    async def get(self, db: AsyncSession, pk: int) -> Draft | None:
        """
        获取草稿箱（灵感快速捕获，轻量独立于正式流水线）

        :param db: 数据库会话
        :param pk: 草稿箱（灵感快速捕获，轻量独立于正式流水线） ID
        :return:
        """
        return await self.select_model(db, pk)

    async def get_select(self) -> Select:
        """获取草稿箱（灵感快速捕获，轻量独立于正式流水线）列表查询表达式"""
        return await self.select_order('id', 'desc')

    async def get_all(self, db: AsyncSession) -> Sequence[Draft]:
        """
        获取所有草稿箱（灵感快速捕获，轻量独立于正式流水线）

        :param db: 数据库会话
        :return:
        """
        return await self.select_models(db)

    async def create(self, db: AsyncSession, obj: CreateDraftParam) -> None:
        """
        创建草稿箱（灵感快速捕获，轻量独立于正式流水线）

        :param db: 数据库会话
        :param obj: 创建草稿箱（灵感快速捕获，轻量独立于正式流水线）参数
        :return:
        """
        await self.create_model(db, obj)

    async def update(self, db: AsyncSession, pk: int, obj: UpdateDraftParam) -> int:
        """
        更新草稿箱（灵感快速捕获，轻量独立于正式流水线）

        :param db: 数据库会话
        :param pk: 草稿箱（灵感快速捕获，轻量独立于正式流水线） ID
        :param obj: 更新 草稿箱（灵感快速捕获，轻量独立于正式流水线）参数
        :return:
        """
        return await self.update_model(db, pk, obj)

    async def delete(self, db: AsyncSession, pks: list[int]) -> int:
        """
        批量删除草稿箱（灵感快速捕获，轻量独立于正式流水线）

        :param db: 数据库会话
        :param pks: 草稿箱（灵感快速捕获，轻量独立于正式流水线） ID 列表
        :return:
        """
        return await self.delete_model_by_column(db, allow_multiple=True, id__in=pks)


draft_dao: CRUDDraft = CRUDDraft(Draft)
