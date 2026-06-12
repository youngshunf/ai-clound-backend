from typing import Any, Sequence

from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.hasn_growth.crud.crud_lead_source_config import lead_source_config_dao
from backend.app.hasn_growth.model import LeadSourceConfig
from backend.app.hasn_growth.schema.lead_source_config import CreateLeadSourceConfigParam, DeleteLeadSourceConfigParam, UpdateLeadSourceConfigParam
from backend.common.exception import errors
from backend.common.pagination import paging_data


class LeadSourceConfigService:
    @staticmethod
    async def get(*, db: AsyncSession, pk: int) -> LeadSourceConfig:
        """
        获取AI lead automation source configuration

        :param db: 数据库会话
        :param pk: AI lead automation source configuration ID
        :return:
        """
        lead_source_config = await lead_source_config_dao.get(db, pk)
        if not lead_source_config:
            raise errors.NotFoundError(msg='AI lead automation source configuration不存在')
        return lead_source_config

    @staticmethod
    async def get_list(db: AsyncSession) -> dict[str, Any]:
        """
        获取AI lead automation source configuration列表

        :param db: 数据库会话
        :return:
        """
        lead_source_config_select = await lead_source_config_dao.get_select()
        return await paging_data(db, lead_source_config_select)

    @staticmethod
    async def get_all(*, db: AsyncSession) -> Sequence[LeadSourceConfig]:
        """
        获取所有AI lead automation source configuration

        :param db: 数据库会话
        :return:
        """
        lead_source_configs = await lead_source_config_dao.get_all(db)
        return lead_source_configs

    @staticmethod
    async def create(*, db: AsyncSession, obj: CreateLeadSourceConfigParam) -> None:
        """
        创建AI lead automation source configuration

        :param db: 数据库会话
        :param obj: 创建AI lead automation source configuration参数
        :return:
        """
        await lead_source_config_dao.create(db, obj)

    @staticmethod
    async def update(*, db: AsyncSession, pk: int, obj: UpdateLeadSourceConfigParam) -> int:
        """
        更新AI lead automation source configuration

        :param db: 数据库会话
        :param pk: AI lead automation source configuration ID
        :param obj: 更新AI lead automation source configuration参数
        :return:
        """
        count = await lead_source_config_dao.update(db, pk, obj)
        return count

    @staticmethod
    async def delete(*, db: AsyncSession, obj: DeleteLeadSourceConfigParam) -> int:
        """
        删除AI lead automation source configuration

        :param db: 数据库会话
        :param obj: AI lead automation source configuration ID 列表
        :return:
        """
        count = await lead_source_config_dao.delete(db, obj.pks)
        return count


lead_source_config_service: LeadSourceConfigService = LeadSourceConfigService()
