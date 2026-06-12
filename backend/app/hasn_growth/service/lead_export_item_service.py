from typing import Any, Sequence

from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.hasn_growth.crud.crud_lead_export_item import lead_export_item_dao
from backend.app.hasn_growth.model import LeadExportItem
from backend.app.hasn_growth.schema.lead_export_item import CreateLeadExportItemParam, DeleteLeadExportItemParam, UpdateLeadExportItemParam
from backend.common.exception import errors
from backend.common.pagination import paging_data


class LeadExportItemService:
    @staticmethod
    async def get(*, db: AsyncSession, pk: int) -> LeadExportItem:
        """
        获取Lead CSV export item snapshot

        :param db: 数据库会话
        :param pk: Lead CSV export item snapshot ID
        :return:
        """
        lead_export_item = await lead_export_item_dao.get(db, pk)
        if not lead_export_item:
            raise errors.NotFoundError(msg='Lead CSV export item snapshot不存在')
        return lead_export_item

    @staticmethod
    async def get_list(db: AsyncSession) -> dict[str, Any]:
        """
        获取Lead CSV export item snapshot列表

        :param db: 数据库会话
        :return:
        """
        lead_export_item_select = await lead_export_item_dao.get_select()
        return await paging_data(db, lead_export_item_select)

    @staticmethod
    async def get_all(*, db: AsyncSession) -> Sequence[LeadExportItem]:
        """
        获取所有Lead CSV export item snapshot

        :param db: 数据库会话
        :return:
        """
        lead_export_items = await lead_export_item_dao.get_all(db)
        return lead_export_items

    @staticmethod
    async def create(*, db: AsyncSession, obj: CreateLeadExportItemParam) -> None:
        """
        创建Lead CSV export item snapshot

        :param db: 数据库会话
        :param obj: 创建Lead CSV export item snapshot参数
        :return:
        """
        await lead_export_item_dao.create(db, obj)

    @staticmethod
    async def update(*, db: AsyncSession, pk: int, obj: UpdateLeadExportItemParam) -> int:
        """
        更新Lead CSV export item snapshot

        :param db: 数据库会话
        :param pk: Lead CSV export item snapshot ID
        :param obj: 更新Lead CSV export item snapshot参数
        :return:
        """
        count = await lead_export_item_dao.update(db, pk, obj)
        return count

    @staticmethod
    async def delete(*, db: AsyncSession, obj: DeleteLeadExportItemParam) -> int:
        """
        删除Lead CSV export item snapshot

        :param db: 数据库会话
        :param obj: Lead CSV export item snapshot ID 列表
        :return:
        """
        count = await lead_export_item_dao.delete(db, obj.pks)
        return count


lead_export_item_service: LeadExportItemService = LeadExportItemService()
