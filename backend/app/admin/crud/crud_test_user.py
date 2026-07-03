from collections.abc import Sequence

from sqlalchemy import Select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy_crud_plus import CRUDPlus

from backend.app.admin.model import TestUser
from backend.app.admin.schema.test_user import CreateTestUserParam, UpdateTestUserParam


class CRUDTestUser(CRUDPlus[TestUser]):
    async def get(self, db: AsyncSession, pk: int) -> TestUser | None:
        """
        获取测试用户

        :param db: 数据库会话
        :param pk: 测试用户 ID
        :return:
        """
        return await self.select_model(db, pk)

    async def get_select(self) -> Select:
        """获取测试用户列表查询表达式"""
        return await self.select_order('id', 'desc')

    async def get_all(self, db: AsyncSession) -> Sequence[TestUser]:
        """
        获取所有测试用户

        :param db: 数据库会话
        :return:
        """
        return await self.select_models(db)

    async def create(self, db: AsyncSession, obj: CreateTestUserParam) -> None:
        """
        创建测试用户

        :param db: 数据库会话
        :param obj: 创建测试用户参数
        :return:
        """
        await self.create_model(db, obj)

    async def update(self, db: AsyncSession, pk: int, obj: UpdateTestUserParam) -> int:
        """
        更新测试用户

        :param db: 数据库会话
        :param pk: 测试用户 ID
        :param obj: 更新 测试用户参数
        :return:
        """
        return await self.update_model(db, pk, obj)

    async def delete(self, db: AsyncSession, pks: list[int]) -> int:
        """
        批量删除测试用户

        :param db: 数据库会话
        :param pks: 测试用户 ID 列表
        :return:
        """
        return await self.delete_model_by_column(db, allow_multiple=True, id__in=pks)


test_user_dao: CRUDTestUser = CRUDTestUser(TestUser)
