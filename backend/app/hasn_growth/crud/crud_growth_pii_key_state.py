from collections.abc import Sequence

from sqlalchemy import Select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy_crud_plus import CRUDPlus

from backend.app.hasn_growth.model import GrowthPiiKeyState
from backend.app.hasn_growth.schema.growth_pii_key_state import (
    CreateGrowthPiiKeyStateParam,
    UpdateGrowthPiiKeyStateParam,
)


class CRUDGrowthPiiKeyState(CRUDPlus[GrowthPiiKeyState]):
    async def get(self, db: AsyncSession, pk: int) -> GrowthPiiKeyState | None:
        """
        获取Growth PII 写入密钥版本单例栅栏

        :param db: 数据库会话
        :param pk: Growth PII 写入密钥版本单例栅栏 ID
        :return:
        """
        return await self.select_model(db, pk)

    async def get_select(self) -> Select:
        """获取Growth PII 写入密钥版本单例栅栏列表查询表达式"""
        return await self.select_order('id', 'desc')

    async def get_all(self, db: AsyncSession) -> Sequence[GrowthPiiKeyState]:
        """
        获取所有Growth PII 写入密钥版本单例栅栏

        :param db: 数据库会话
        :return:
        """
        return await self.select_models(db)

    async def create(self, db: AsyncSession, obj: CreateGrowthPiiKeyStateParam) -> None:
        """
        创建Growth PII 写入密钥版本单例栅栏

        :param db: 数据库会话
        :param obj: 创建Growth PII 写入密钥版本单例栅栏参数
        :return:
        """
        await self.create_model(db, obj)

    async def update(self, db: AsyncSession, pk: int, obj: UpdateGrowthPiiKeyStateParam) -> int:
        """
        更新Growth PII 写入密钥版本单例栅栏

        :param db: 数据库会话
        :param pk: Growth PII 写入密钥版本单例栅栏 ID
        :param obj: 更新 Growth PII 写入密钥版本单例栅栏参数
        :return:
        """
        return await self.update_model(db, pk, obj)

    async def delete(self, db: AsyncSession, pks: list[int]) -> int:
        """
        批量删除Growth PII 写入密钥版本单例栅栏

        :param db: 数据库会话
        :param pks: Growth PII 写入密钥版本单例栅栏 ID 列表
        :return:
        """
        return await self.delete_model_by_column(db, allow_multiple=True, id__in=pks)


growth_pii_key_state_dao: CRUDGrowthPiiKeyState = CRUDGrowthPiiKeyState(GrowthPiiKeyState)
