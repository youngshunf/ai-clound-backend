from collections.abc import Sequence
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.hasn_task.crud.crud_skill_bundle import hasn_skill_bundle_dao
from backend.app.hasn_task.model.skill_bundle import HasnSkillBundle
from backend.app.hasn_task.schema.skill_bundle import (
    CreateHasnSkillBundleParam,
    DeleteHasnSkillBundleParam,
    UpdateHasnSkillBundleParam,
)
from backend.common.exception import errors
from backend.common.pagination import paging_data


class HasnSkillBundleService:
    @staticmethod
    async def get(*, db: AsyncSession, pk: int) -> HasnSkillBundle:
        """
        获取Skill Bundle 定义表（多个 skill 的组合）

        :param db: 数据库会话
        :param pk: Skill Bundle 定义表（多个 skill 的组合） ID
        :return:
        """
        hasn_skill_bundle = await hasn_skill_bundle_dao.get(db, pk)
        if not hasn_skill_bundle:
            raise errors.NotFoundError(msg='Skill Bundle 定义表（多个 skill 的组合）不存在')
        return hasn_skill_bundle

    @staticmethod
    async def get_list(db: AsyncSession) -> dict[str, Any]:
        """
        获取Skill Bundle 定义表（多个 skill 的组合）列表

        :param db: 数据库会话
        :return:
        """
        hasn_skill_bundle_select = await hasn_skill_bundle_dao.get_select()
        return await paging_data(db, hasn_skill_bundle_select)

    @staticmethod
    async def get_list_by_owner(db: AsyncSession, owner_id: str) -> dict[str, Any]:
        """获取指定 owner 的 Skill Bundle 列表"""
        select_stmt = select(HasnSkillBundle).where(HasnSkillBundle.owner_id == owner_id)
        return await paging_data(db, select_stmt)

    @staticmethod
    async def get_all(*, db: AsyncSession) -> Sequence[HasnSkillBundle]:
        """
        获取所有Skill Bundle 定义表（多个 skill 的组合）

        :param db: 数据库会话
        :return:
        """
        hasn_skill_bundle_list = await hasn_skill_bundle_dao.get_all(db)
        return hasn_skill_bundle_list

    @staticmethod
    async def create(*, db: AsyncSession, obj: CreateHasnSkillBundleParam) -> None:
        """
        创建Skill Bundle 定义表（多个 skill 的组合）

        :param db: 数据库会话
        :param obj: 创建Skill Bundle 定义表（多个 skill 的组合）参数
        :return:
        """
        await hasn_skill_bundle_dao.create(db, obj)

    @staticmethod
    async def update(*, db: AsyncSession, pk: int, obj: UpdateHasnSkillBundleParam) -> int:
        """
        更新Skill Bundle 定义表（多个 skill 的组合）

        :param db: 数据库会话
        :param pk: Skill Bundle 定义表（多个 skill 的组合） ID
        :param obj: 更新Skill Bundle 定义表（多个 skill 的组合）参数
        :return:
        """
        count = await hasn_skill_bundle_dao.update(db, pk, obj)
        return count

    @staticmethod
    async def delete(*, db: AsyncSession, obj: DeleteHasnSkillBundleParam) -> int:
        """
        删除Skill Bundle 定义表（多个 skill 的组合）

        :param db: 数据库会话
        :param obj: Skill Bundle 定义表（多个 skill 的组合） ID 列表
        :return:
        """
        count = await hasn_skill_bundle_dao.delete(db, obj.pks)
        return count


hasn_skill_bundle_service: HasnSkillBundleService = HasnSkillBundleService()
