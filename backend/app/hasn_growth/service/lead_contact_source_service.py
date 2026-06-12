from typing import Any, Sequence

from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.hasn_growth.crud.crud_lead_contact_source import lead_contact_source_dao
from backend.app.hasn_growth.model import LeadContactSource
from backend.app.hasn_growth.schema.lead_contact_source import CreateLeadContactSourceParam, DeleteLeadContactSourceParam, UpdateLeadContactSourceParam
from backend.common.exception import errors
from backend.common.pagination import paging_data


class LeadContactSourceService:
    @staticmethod
    async def get(*, db: AsyncSession, pk: int) -> LeadContactSource:
        """
        获取Lead multi-source evidence

        :param db: 数据库会话
        :param pk: Lead multi-source evidence ID
        :return:
        """
        lead_contact_source = await lead_contact_source_dao.get(db, pk)
        if not lead_contact_source:
            raise errors.NotFoundError(msg='Lead multi-source evidence不存在')
        return lead_contact_source

    @staticmethod
    async def get_list(db: AsyncSession) -> dict[str, Any]:
        """
        获取Lead multi-source evidence列表

        :param db: 数据库会话
        :return:
        """
        lead_contact_source_select = await lead_contact_source_dao.get_select()
        return await paging_data(db, lead_contact_source_select)

    @staticmethod
    async def get_all(*, db: AsyncSession) -> Sequence[LeadContactSource]:
        """
        获取所有Lead multi-source evidence

        :param db: 数据库会话
        :return:
        """
        lead_contact_sources = await lead_contact_source_dao.get_all(db)
        return lead_contact_sources

    @staticmethod
    async def create(*, db: AsyncSession, obj: CreateLeadContactSourceParam) -> None:
        """
        创建Lead multi-source evidence

        :param db: 数据库会话
        :param obj: 创建Lead multi-source evidence参数
        :return:
        """
        await lead_contact_source_dao.create(db, obj)

    @staticmethod
    async def update(*, db: AsyncSession, pk: int, obj: UpdateLeadContactSourceParam) -> int:
        """
        更新Lead multi-source evidence

        :param db: 数据库会话
        :param pk: Lead multi-source evidence ID
        :param obj: 更新Lead multi-source evidence参数
        :return:
        """
        count = await lead_contact_source_dao.update(db, pk, obj)
        return count

    @staticmethod
    async def delete(*, db: AsyncSession, obj: DeleteLeadContactSourceParam) -> int:
        """
        删除Lead multi-source evidence

        :param db: 数据库会话
        :param obj: Lead multi-source evidence ID 列表
        :return:
        """
        count = await lead_contact_source_dao.delete(db, obj.pks)
        return count


lead_contact_source_service: LeadContactSourceService = LeadContactSourceService()
