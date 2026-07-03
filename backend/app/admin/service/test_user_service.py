from collections.abc import Sequence
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.admin.crud.crud_test_user import test_user_dao
from backend.app.admin.model import TestUser
from backend.app.admin.schema.test_user import CreateTestUserParam, DeleteTestUserParam, UpdateTestUserParam
from backend.common.exception import errors
from backend.common.pagination import paging_data


class TestUserService:
    @staticmethod
    async def get(*, db: AsyncSession, pk: int) -> TestUser:
        """
        获取测试用户

        :param db: 数据库会话
        :param pk: 测试用户 ID
        :return:
        """
        test_user = await test_user_dao.get(db, pk)
        if not test_user:
            raise errors.NotFoundError(msg='测试用户不存在')
        return test_user

    @staticmethod
    async def get_list(db: AsyncSession) -> dict[str, Any]:
        """
        获取测试用户列表

        :param db: 数据库会话
        :return:
        """
        test_user_select = await test_user_dao.get_select()
        return await paging_data(db, test_user_select)

    @staticmethod
    async def get_all(*, db: AsyncSession) -> Sequence[TestUser]:
        """
        获取所有测试用户

        :param db: 数据库会话
        :return:
        """
        test_users = await test_user_dao.get_all(db)
        return test_users

    @staticmethod
    async def create(*, db: AsyncSession, obj: CreateTestUserParam) -> None:
        """
        创建测试用户

        :param db: 数据库会话
        :param obj: 创建测试用户参数
        :return:
        """
        await test_user_dao.create(db, obj)

    @staticmethod
    async def update(*, db: AsyncSession, pk: int, obj: UpdateTestUserParam) -> int:
        """
        更新测试用户

        :param db: 数据库会话
        :param pk: 测试用户 ID
        :param obj: 更新测试用户参数
        :return:
        """
        count = await test_user_dao.update(db, pk, obj)
        return count

    @staticmethod
    async def delete(*, db: AsyncSession, obj: DeleteTestUserParam) -> int:
        """
        删除测试用户

        :param db: 数据库会话
        :param obj: 测试用户 ID 列表
        :return:
        """
        count = await test_user_dao.delete(db, obj.pks)
        return count


test_user_service: TestUserService = TestUserService()
