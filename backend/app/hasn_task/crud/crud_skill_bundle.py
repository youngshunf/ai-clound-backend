from collections.abc import Sequence
from typing import cast

from sqlalchemy import Select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy_crud_plus import CRUDPlus

from backend.app.hasn_task.model.skill_bundle import HasnSkillBundle
from backend.app.hasn_task.schema.skill_bundle import CreateHasnSkillBundleParam, UpdateHasnSkillBundleParam


class CRUDHasnSkillBundle(CRUDPlus[HasnSkillBundle]):
    async def get(self, db: AsyncSession, pk: int) -> HasnSkillBundle | None:
        """
        获取Skill Bundle 定义表（多个 skill 的组合）

        :param db: 数据库会话
        :param pk: Skill Bundle 定义表（多个 skill 的组合） ID
        :return:
        """
        return cast(HasnSkillBundle | None, await self.select_model(db, pk))

    async def get_select(self) -> Select:
        """获取Skill Bundle 定义表（多个 skill 的组合）列表查询表达式"""
        return await self.select_order('id', 'desc')

    async def get_all(self, db: AsyncSession) -> Sequence[HasnSkillBundle]:
        """
        获取所有Skill Bundle 定义表（多个 skill 的组合）

        :param db: 数据库会话
        :return:
        """
        return cast(Sequence[HasnSkillBundle], await self.select_models(db))

    async def create(self, db: AsyncSession, obj: CreateHasnSkillBundleParam) -> HasnSkillBundle:
        """
        创建Skill Bundle 定义表（多个 skill 的组合）

        :param db: 数据库会话
        :param obj: 创建Skill Bundle 定义表（多个 skill 的组合）参数
        :return:
        """
        return await self.create_model(db, obj, flush=True)

    async def update(self, db: AsyncSession, pk: int, obj: UpdateHasnSkillBundleParam) -> int:
        """
        更新Skill Bundle 定义表（多个 skill 的组合）

        :param db: 数据库会话
        :param pk: Skill Bundle 定义表（多个 skill 的组合） ID
        :param obj: 更新 Skill Bundle 定义表（多个 skill 的组合）参数
        :return:
        """
        return await self.update_model(db, pk, obj)

    async def delete(self, db: AsyncSession, pks: list[int]) -> int:
        """
        批量删除Skill Bundle 定义表（多个 skill 的组合）

        :param db: 数据库会话
        :param pks: Skill Bundle 定义表（多个 skill 的组合） ID 列表
        :return:
        """
        return await self.delete_model_by_column(db, allow_multiple=True, id__in=pks)


hasn_skill_bundle_dao: CRUDHasnSkillBundle = CRUDHasnSkillBundle(HasnSkillBundle)
