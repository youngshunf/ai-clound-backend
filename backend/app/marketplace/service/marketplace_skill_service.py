from typing import Any, Sequence

from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.marketplace.crud.crud_marketplace_skill import marketplace_skill_dao
from backend.app.marketplace.model import MarketplaceSkill
from backend.app.marketplace.schema.marketplace_skill import CreateMarketplaceSkillParam, DeleteMarketplaceSkillParam, UpdateMarketplaceSkillParam
from backend.common.exception import errors
from backend.common.pagination import paging_data


class MarketplaceSkillService:
    @staticmethod
    async def get(*, db: AsyncSession, pk: int) -> MarketplaceSkill:
        """
        获取技能市场技能

        :param db: 数据库会话
        :param pk: 技能市场技能 ID
        :return:
        """
        marketplace_skill = await marketplace_skill_dao.get(db, pk)
        if not marketplace_skill:
            raise errors.NotFoundError(msg='技能市场技能不存在')
        return marketplace_skill

    @staticmethod
    async def get_list(db: AsyncSession) -> dict[str, Any]:
        """
        获取技能市场技能列表

        :param db: 数据库会话
        :return:
        """
        marketplace_skill_select = await marketplace_skill_dao.get_select()
        return await paging_data(db, marketplace_skill_select)

    @staticmethod
    async def get_all(*, db: AsyncSession) -> Sequence[MarketplaceSkill]:
        """
        获取所有技能市场技能

        :param db: 数据库会话
        :return:
        """
        marketplace_skills = await marketplace_skill_dao.get_all(db)
        return marketplace_skills

    @staticmethod
    async def create(*, db: AsyncSession, obj: CreateMarketplaceSkillParam) -> None:
        """
        创建技能市场技能

        :param db: 数据库会话
        :param obj: 创建技能市场技能参数
        :return:
        """
        await marketplace_skill_dao.create(db, obj)

    @staticmethod
    async def update(*, db: AsyncSession, pk: int, obj: UpdateMarketplaceSkillParam) -> int:
        """
        更新技能市场技能

        :param db: 数据库会话
        :param pk: 技能市场技能 ID
        :param obj: 更新技能市场技能参数
        :return:
        """
        count = await marketplace_skill_dao.update(db, pk, obj)
        return count

    @staticmethod
    async def delete(*, db: AsyncSession, obj: DeleteMarketplaceSkillParam) -> int:
        """
        删除技能市场技能

        :param db: 数据库会话
        :param obj: 技能市场技能 ID 列表
        :return:
        """
        count = await marketplace_skill_dao.delete(db, obj.pks)
        return count


marketplace_skill_service: MarketplaceSkillService = MarketplaceSkillService()
