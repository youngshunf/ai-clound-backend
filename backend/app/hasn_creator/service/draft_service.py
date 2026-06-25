from collections.abc import Sequence
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.hasn_creator.crud.crud_draft import draft_dao
from backend.app.hasn_creator.model import Draft
from backend.app.hasn_creator.schema.draft import CreateDraftParam, DeleteDraftParam, UpdateDraftParam
from backend.common.exception import errors
from backend.common.pagination import paging_data


class DraftService:
    @staticmethod
    async def get(*, db: AsyncSession, pk: int) -> Draft:
        """
        获取草稿箱（灵感快速捕获，轻量独立于正式流水线）

        :param db: 数据库会话
        :param pk: 草稿箱（灵感快速捕获，轻量独立于正式流水线） ID
        :return:
        """
        draft = await draft_dao.get(db, pk)
        if not draft:
            raise errors.NotFoundError(msg='草稿箱（灵感快速捕获，轻量独立于正式流水线）不存在')
        return draft

    @staticmethod
    async def get_list(db: AsyncSession) -> dict[str, Any]:
        """
        获取草稿箱（灵感快速捕获，轻量独立于正式流水线）列表

        :param db: 数据库会话
        :return:
        """
        draft_select = await draft_dao.get_select()
        return await paging_data(db, draft_select)

    @staticmethod
    async def get_all(*, db: AsyncSession) -> Sequence[Draft]:
        """
        获取所有草稿箱（灵感快速捕获，轻量独立于正式流水线）

        :param db: 数据库会话
        :return:
        """
        draft_list = await draft_dao.get_all(db)
        return draft_list

    @staticmethod
    async def create(*, db: AsyncSession, obj: CreateDraftParam) -> None:
        """
        创建草稿箱（灵感快速捕获，轻量独立于正式流水线）

        :param db: 数据库会话
        :param obj: 创建草稿箱（灵感快速捕获，轻量独立于正式流水线）参数
        :return:
        """
        await draft_dao.create(db, obj)

    @staticmethod
    async def update(*, db: AsyncSession, pk: int, obj: UpdateDraftParam) -> int:
        """
        更新草稿箱（灵感快速捕获，轻量独立于正式流水线）

        :param db: 数据库会话
        :param pk: 草稿箱（灵感快速捕获，轻量独立于正式流水线） ID
        :param obj: 更新草稿箱（灵感快速捕获，轻量独立于正式流水线）参数
        :return:
        """
        count = await draft_dao.update(db, pk, obj)
        return count

    @staticmethod
    async def delete(*, db: AsyncSession, obj: DeleteDraftParam) -> int:
        """
        删除草稿箱（灵感快速捕获，轻量独立于正式流水线）

        :param db: 数据库会话
        :param obj: 草稿箱（灵感快速捕获，轻量独立于正式流水线） ID 列表
        :return:
        """
        count = await draft_dao.delete(db, obj.pks)
        return count


draft_service: DraftService = DraftService()
