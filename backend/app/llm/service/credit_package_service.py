from typing import Any, Sequence

from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.llm.crud.crud_credit_package import credit_package_dao
from backend.app.llm.model import CreditPackage
from backend.app.llm.schema.credit_package import CreateCreditPackageParam, DeleteCreditPackageParam, UpdateCreditPackageParam
from backend.common.exception import errors
from backend.common.pagination import paging_data


class CreditPackageService:
    @staticmethod
    async def get(*, db: AsyncSession, pk: int) -> CreditPackage:
        """
        获取积分包配置表 - 定义可购买的积分包

        :param db: 数据库会话
        :param pk: 积分包配置表 - 定义可购买的积分包 ID
        :return:
        """
        credit_package = await credit_package_dao.get(db, pk)
        if not credit_package:
            raise errors.NotFoundError(msg='积分包配置表 - 定义可购买的积分包不存在')
        return credit_package

    @staticmethod
    async def get_list(db: AsyncSession) -> dict[str, Any]:
        """
        获取积分包配置表 - 定义可购买的积分包列表

        :param db: 数据库会话
        :return:
        """
        credit_package_select = await credit_package_dao.get_select()
        return await paging_data(db, credit_package_select)

    @staticmethod
    async def get_all(*, db: AsyncSession) -> Sequence[CreditPackage]:
        """
        获取所有积分包配置表 - 定义可购买的积分包

        :param db: 数据库会话
        :return:
        """
        credit_packages = await credit_package_dao.get_all(db)
        return credit_packages

    @staticmethod
    async def create(*, db: AsyncSession, obj: CreateCreditPackageParam) -> None:
        """
        创建积分包配置表 - 定义可购买的积分包

        :param db: 数据库会话
        :param obj: 创建积分包配置表 - 定义可购买的积分包参数
        :return:
        """
        await credit_package_dao.create(db, obj)

    @staticmethod
    async def update(*, db: AsyncSession, pk: int, obj: UpdateCreditPackageParam) -> int:
        """
        更新积分包配置表 - 定义可购买的积分包

        :param db: 数据库会话
        :param pk: 积分包配置表 - 定义可购买的积分包 ID
        :param obj: 更新积分包配置表 - 定义可购买的积分包参数
        :return:
        """
        count = await credit_package_dao.update(db, pk, obj)
        return count

    @staticmethod
    async def delete(*, db: AsyncSession, obj: DeleteCreditPackageParam) -> int:
        """
        删除积分包配置表 - 定义可购买的积分包

        :param db: 数据库会话
        :param obj: 积分包配置表 - 定义可购买的积分包 ID 列表
        :return:
        """
        count = await credit_package_dao.delete(db, obj.pks)
        return count


credit_package_service: CreditPackageService = CreditPackageService()
