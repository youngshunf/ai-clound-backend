from collections.abc import Sequence

from sqlalchemy import Select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy_crud_plus import CRUDPlus

from backend.app.hasn_growth.model import LeadExportItem
from backend.app.hasn_growth.schema.lead_export_item import CreateLeadExportItemParam, UpdateLeadExportItemParam


class CRUDLeadExportItem(CRUDPlus[LeadExportItem]):
    async def get(self, db: AsyncSession, pk: int) -> LeadExportItem | None:
        """
        获取Lead CSV export item snapshot

        :param db: 数据库会话
        :param pk: Lead CSV export item snapshot ID
        :return:
        """
        return await self.select_model(db, pk)

    async def get_select(self) -> Select:
        """获取Lead CSV export item snapshot列表查询表达式"""
        return await self.select_order('id', 'desc')

    async def get_all(self, db: AsyncSession) -> Sequence[LeadExportItem]:
        """
        获取所有Lead CSV export item snapshot

        :param db: 数据库会话
        :return:
        """
        return await self.select_models(db)

    async def create(self, db: AsyncSession, obj: CreateLeadExportItemParam) -> None:
        """
        创建Lead CSV export item snapshot

        :param db: 数据库会话
        :param obj: 创建Lead CSV export item snapshot参数
        :return:
        """
        await self.create_model(db, obj)

    async def update(self, db: AsyncSession, pk: int, obj: UpdateLeadExportItemParam) -> int:
        """
        更新Lead CSV export item snapshot

        :param db: 数据库会话
        :param pk: Lead CSV export item snapshot ID
        :param obj: 更新 Lead CSV export item snapshot参数
        :return:
        """
        return await self.update_model(db, pk, obj)

    async def delete(self, db: AsyncSession, pks: list[int]) -> int:
        """
        批量删除Lead CSV export item snapshot

        :param db: 数据库会话
        :param pks: Lead CSV export item snapshot ID 列表
        :return:
        """
        return await self.delete_model_by_column(db, allow_multiple=True, id__in=pks)


lead_export_item_dao: CRUDLeadExportItem = CRUDLeadExportItem(LeadExportItem)
