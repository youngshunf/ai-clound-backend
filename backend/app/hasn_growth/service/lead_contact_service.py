from collections.abc import Sequence
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.hasn_growth.crud.crud_lead_contact import lead_contact_dao
from backend.app.hasn_growth.model import LeadContact
from backend.app.hasn_growth.schema.lead_contact import (
    CreateLeadContactParam,
    DeleteLeadContactParam,
    UpdateLeadContactParam,
)
from backend.common.exception import errors
from backend.common.pagination import paging_data


class LeadContactService:
    @staticmethod
    async def get(*, db: AsyncSession, pk: int) -> LeadContact:
        """
        获取Valid deduplicated lead contact

        :param db: 数据库会话
        :param pk: Valid deduplicated lead contact ID
        :return:
        """
        lead_contact = await lead_contact_dao.get(db, pk)
        if not lead_contact:
            raise errors.NotFoundError(msg='Valid deduplicated lead contact不存在')
        return lead_contact

    @staticmethod
    async def get_list(db: AsyncSession) -> dict[str, Any]:
        """
        获取Valid deduplicated lead contact列表

        :param db: 数据库会话
        :return:
        """
        lead_contact_select = await lead_contact_dao.get_select()
        return await paging_data(db, lead_contact_select)

    @staticmethod
    async def get_all(*, db: AsyncSession) -> Sequence[LeadContact]:
        """
        获取所有Valid deduplicated lead contact

        :param db: 数据库会话
        :return:
        """
        lead_contacts = await lead_contact_dao.get_all(db)
        return lead_contacts

    @staticmethod
    async def create(*, db: AsyncSession, obj: CreateLeadContactParam) -> None:
        """
        创建Valid deduplicated lead contact

        :param db: 数据库会话
        :param obj: 创建Valid deduplicated lead contact参数
        :return:
        """
        await lead_contact_dao.create(db, obj)

    @staticmethod
    async def update(*, db: AsyncSession, pk: int, obj: UpdateLeadContactParam) -> int:
        """
        更新Valid deduplicated lead contact

        :param db: 数据库会话
        :param pk: Valid deduplicated lead contact ID
        :param obj: 更新Valid deduplicated lead contact参数
        :return:
        """
        count = await lead_contact_dao.update(db, pk, obj)
        return count

    @staticmethod
    async def delete(*, db: AsyncSession, obj: DeleteLeadContactParam) -> int:
        """
        删除Valid deduplicated lead contact

        :param db: 数据库会话
        :param obj: Valid deduplicated lead contact ID 列表
        :return:
        """
        count = await lead_contact_dao.delete(db, obj.pks)
        return count


lead_contact_service: LeadContactService = LeadContactService()
