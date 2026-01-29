from typing import Any, Sequence

from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.marketplace.crud.crud_marketplace_skill_version import marketplace_skill_version_dao
from backend.app.marketplace.model import MarketplaceSkillVersion
from backend.app.marketplace.schema.marketplace_skill_version import CreateMarketplaceSkillVersionParam, DeleteMarketplaceSkillVersionParam, UpdateMarketplaceSkillVersionParam
from backend.common.exception import errors
from backend.common.pagination import paging_data


class MarketplaceSkillVersionService:
    @staticmethod
    async def get(*, db: AsyncSession, pk: int) -> MarketplaceSkillVersion:
        """
        获取技能版本

        :param db: 数据库会话
        :param pk: 技能版本 ID
        :return:
        """
        marketplace_skill_version = await marketplace_skill_version_dao.get(db, pk)
        if not marketplace_skill_version:
            raise errors.NotFoundError(msg='技能版本不存在')
        return marketplace_skill_version

    @staticmethod
    async def get_list(db: AsyncSession) -> dict[str, Any]:
        """
        获取技能版本列表

        :param db: 数据库会话
        :return:
        """
        marketplace_skill_version_select = await marketplace_skill_version_dao.get_select()
        return await paging_data(db, marketplace_skill_version_select)

    @staticmethod
    async def get_all(*, db: AsyncSession) -> Sequence[MarketplaceSkillVersion]:
        """
        获取所有技能版本

        :param db: 数据库会话
        :return:
        """
        marketplace_skill_versions = await marketplace_skill_version_dao.get_all(db)
        return marketplace_skill_versions

    @staticmethod
    async def create(*, db: AsyncSession, obj: CreateMarketplaceSkillVersionParam) -> None:
        """
        创建技能版本

        :param db: 数据库会话
        :param obj: 创建技能版本参数
        :return:
        """
        await marketplace_skill_version_dao.create(db, obj)

    @staticmethod
    async def update(*, db: AsyncSession, pk: int, obj: UpdateMarketplaceSkillVersionParam) -> int:
        """
        更新技能版本

        :param db: 数据库会话
        :param pk: 技能版本 ID
        :param obj: 更新技能版本参数
        :return:
        """
        count = await marketplace_skill_version_dao.update(db, pk, obj)
        return count

    @staticmethod
    async def delete(*, db: AsyncSession, obj: DeleteMarketplaceSkillVersionParam) -> int:
        """
        删除技能版本

        :param db: 数据库会话
        :param obj: 技能版本 ID 列表
        :return:
        """
        count = await marketplace_skill_version_dao.delete(db, obj.pks)
        return count


marketplace_skill_version_service: MarketplaceSkillVersionService = MarketplaceSkillVersionService()
