from typing import Sequence

from sqlalchemy import Select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy_crud_plus import CRUDPlus

from backend.app.hasn_finance.model import WatchBriefing
from backend.app.hasn_finance.schema.watch_briefing import CreateWatchBriefingParam, UpdateWatchBriefingParam


class CRUDWatchBriefing(CRUDPlus[WatchBriefing]):
    async def get(self, db: AsyncSession, pk: int) -> WatchBriefing | None:
        """
        获取盯盘简报（流程 D·产物·同事务登记 hasn_artifacts；流程 D 不属第一版上线门，05 §3.1.7）

        :param db: 数据库会话
        :param pk: 盯盘简报（流程 D·产物·同事务登记 hasn_artifacts；流程 D 不属第一版上线门，05 §3.1.7） ID
        :return:
        """
        return await self.select_model(db, pk)

    async def get_select(self) -> Select:
        """获取盯盘简报（流程 D·产物·同事务登记 hasn_artifacts；流程 D 不属第一版上线门，05 §3.1.7）列表查询表达式"""
        return await self.select_order('id', 'desc')

    async def get_all(self, db: AsyncSession) -> Sequence[WatchBriefing]:
        """
        获取所有盯盘简报（流程 D·产物·同事务登记 hasn_artifacts；流程 D 不属第一版上线门，05 §3.1.7）

        :param db: 数据库会话
        :return:
        """
        return await self.select_models(db)

    async def create(self, db: AsyncSession, obj: CreateWatchBriefingParam) -> None:
        """
        创建盯盘简报（流程 D·产物·同事务登记 hasn_artifacts；流程 D 不属第一版上线门，05 §3.1.7）

        :param db: 数据库会话
        :param obj: 创建盯盘简报（流程 D·产物·同事务登记 hasn_artifacts；流程 D 不属第一版上线门，05 §3.1.7）参数
        :return:
        """
        await self.create_model(db, obj)

    async def update(self, db: AsyncSession, pk: int, obj: UpdateWatchBriefingParam) -> int:
        """
        更新盯盘简报（流程 D·产物·同事务登记 hasn_artifacts；流程 D 不属第一版上线门，05 §3.1.7）

        :param db: 数据库会话
        :param pk: 盯盘简报（流程 D·产物·同事务登记 hasn_artifacts；流程 D 不属第一版上线门，05 §3.1.7） ID
        :param obj: 更新 盯盘简报（流程 D·产物·同事务登记 hasn_artifacts；流程 D 不属第一版上线门，05 §3.1.7）参数
        :return:
        """
        return await self.update_model(db, pk, obj)

    async def delete(self, db: AsyncSession, pks: list[int]) -> int:
        """
        批量删除盯盘简报（流程 D·产物·同事务登记 hasn_artifacts；流程 D 不属第一版上线门，05 §3.1.7）

        :param db: 数据库会话
        :param pks: 盯盘简报（流程 D·产物·同事务登记 hasn_artifacts；流程 D 不属第一版上线门，05 §3.1.7） ID 列表
        :return:
        """
        return await self.delete_model_by_column(db, allow_multiple=True, id__in=pks)


watch_briefing_dao: CRUDWatchBriefing = CRUDWatchBriefing(WatchBriefing)
