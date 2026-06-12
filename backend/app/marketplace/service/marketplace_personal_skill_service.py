from typing import Any, Sequence

from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.marketplace.crud.crud_marketplace_personal_skill import marketplace_personal_skill_dao
from backend.app.marketplace.model import MarketplacePersonalSkill
from backend.app.marketplace.schema.marketplace_personal_skill import CreateMarketplacePersonalSkillParam, DeleteMarketplacePersonalSkillParam, UpdateMarketplacePersonalSkillParam
from backend.common.exception import errors
from backend.common.pagination import paging_data


class MarketplacePersonalSkillService:
    @staticmethod
    async def get(*, db: AsyncSession, pk: int) -> MarketplacePersonalSkill:
        """
        获取个人技能同步表（个人技能库 SSOT）

        :param db: 数据库会话
        :param pk: 个人技能同步表（个人技能库 SSOT） ID
        :return:
        """
        marketplace_personal_skill = await marketplace_personal_skill_dao.get(db, pk)
        if not marketplace_personal_skill:
            raise errors.NotFoundError(msg='个人技能同步表（个人技能库 SSOT）不存在')
        return marketplace_personal_skill

    @staticmethod
    async def get_list(db: AsyncSession) -> dict[str, Any]:
        """
        获取个人技能同步表（个人技能库 SSOT）列表

        :param db: 数据库会话
        :return:
        """
        marketplace_personal_skill_select = await marketplace_personal_skill_dao.get_select()
        return await paging_data(db, marketplace_personal_skill_select)

    @staticmethod
    async def get_all(*, db: AsyncSession) -> Sequence[MarketplacePersonalSkill]:
        """
        获取所有个人技能同步表（个人技能库 SSOT）

        :param db: 数据库会话
        :return:
        """
        marketplace_personal_skill_list = await marketplace_personal_skill_dao.get_all(db)
        return marketplace_personal_skill_list

    @staticmethod
    async def create(*, db: AsyncSession, obj: CreateMarketplacePersonalSkillParam) -> None:
        """
        创建个人技能同步表（个人技能库 SSOT）

        :param db: 数据库会话
        :param obj: 创建个人技能同步表（个人技能库 SSOT）参数
        :return:
        """
        await marketplace_personal_skill_dao.create(db, obj)

    @staticmethod
    async def update(*, db: AsyncSession, pk: int, obj: UpdateMarketplacePersonalSkillParam) -> int:
        """
        更新个人技能同步表（个人技能库 SSOT）

        :param db: 数据库会话
        :param pk: 个人技能同步表（个人技能库 SSOT） ID
        :param obj: 更新个人技能同步表（个人技能库 SSOT）参数
        :return:
        """
        count = await marketplace_personal_skill_dao.update(db, pk, obj)
        return count

    @staticmethod
    async def delete(*, db: AsyncSession, obj: DeleteMarketplacePersonalSkillParam) -> int:
        """
        删除个人技能同步表（个人技能库 SSOT）

        :param db: 数据库会话
        :param obj: 个人技能同步表（个人技能库 SSOT） ID 列表
        :return:
        """
        count = await marketplace_personal_skill_dao.delete(db, obj.pks)
        return count


marketplace_personal_skill_service: MarketplacePersonalSkillService = MarketplacePersonalSkillService()
