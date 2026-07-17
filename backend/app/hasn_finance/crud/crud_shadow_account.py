from typing import Sequence

from sqlalchemy import Select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy_crud_plus import CRUDPlus

from backend.app.hasn_finance.model import ShadowAccount
from backend.app.hasn_finance.schema.shadow_account import CreateShadowAccountParam, UpdateShadowAccountParam


class CRUDShadowAccount(CRUDPlus[ShadowAccount]):
    async def get(self, db: AsyncSession, pk: int) -> ShadowAccount | None:
        """
        获取影子账户（流程 C·产物+容器·隐私最敏感·可挂平台项目，05 §3.1.5）

        :param db: 数据库会话
        :param pk: 影子账户（流程 C·产物+容器·隐私最敏感·可挂平台项目，05 §3.1.5） ID
        :return:
        """
        return await self.select_model(db, pk)

    async def get_select(self) -> Select:
        """获取影子账户（流程 C·产物+容器·隐私最敏感·可挂平台项目，05 §3.1.5）列表查询表达式"""
        return await self.select_order('id', 'desc')

    async def get_all(self, db: AsyncSession) -> Sequence[ShadowAccount]:
        """
        获取所有影子账户（流程 C·产物+容器·隐私最敏感·可挂平台项目，05 §3.1.5）

        :param db: 数据库会话
        :return:
        """
        return await self.select_models(db)

    async def create(self, db: AsyncSession, obj: CreateShadowAccountParam) -> None:
        """
        创建影子账户（流程 C·产物+容器·隐私最敏感·可挂平台项目，05 §3.1.5）

        :param db: 数据库会话
        :param obj: 创建影子账户（流程 C·产物+容器·隐私最敏感·可挂平台项目，05 §3.1.5）参数
        :return:
        """
        await self.create_model(db, obj)

    async def update(self, db: AsyncSession, pk: int, obj: UpdateShadowAccountParam) -> int:
        """
        更新影子账户（流程 C·产物+容器·隐私最敏感·可挂平台项目，05 §3.1.5）

        :param db: 数据库会话
        :param pk: 影子账户（流程 C·产物+容器·隐私最敏感·可挂平台项目，05 §3.1.5） ID
        :param obj: 更新 影子账户（流程 C·产物+容器·隐私最敏感·可挂平台项目，05 §3.1.5）参数
        :return:
        """
        return await self.update_model(db, pk, obj)

    async def delete(self, db: AsyncSession, pks: list[int]) -> int:
        """
        批量删除影子账户（流程 C·产物+容器·隐私最敏感·可挂平台项目，05 §3.1.5）

        :param db: 数据库会话
        :param pks: 影子账户（流程 C·产物+容器·隐私最敏感·可挂平台项目，05 §3.1.5） ID 列表
        :return:
        """
        return await self.delete_model_by_column(db, allow_multiple=True, id__in=pks)


shadow_account_dao: CRUDShadowAccount = CRUDShadowAccount(ShadowAccount)
