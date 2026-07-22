from collections.abc import Sequence
from typing import Any, cast

from sqlalchemy import Select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy_crud_plus import CRUDPlus

from backend.plugin.code_generator.model import GenBusiness
from backend.plugin.code_generator.schema.business import CreateGenBusinessParam, UpdateGenBusinessParam


class CRUDGenBusiness(CRUDPlus[GenBusiness]):
    """代码生成业务 CRUD 类"""

    @staticmethod
    def _single_business(result: object) -> GenBusiness | None:
        """将不带关联加载的查询结果收紧为单个代码生成业务。"""
        if result is not None and not isinstance(result, GenBusiness):
            raise TypeError('代码生成业务单模型查询返回了关联结果')
        return cast(GenBusiness | None, result)

    @staticmethod
    def _business_sequence(result: object) -> Sequence[GenBusiness]:
        """将不带关联加载的查询结果收紧为代码生成业务序列。"""
        if not isinstance(result, Sequence) or not all(isinstance(item, GenBusiness) for item in result):
            raise TypeError('代码生成业务列表查询返回了关联结果')
        return cast(Sequence[GenBusiness], result)

    async def get(self, db: AsyncSession, pk: int) -> GenBusiness | None:
        """
        获取代码生成业务

        :param db: 数据库会话
        :param pk: 代码生成业务 ID
        :return:
        """
        return self._single_business(await self.select_model(db, pk))

    async def get_by_name(self, db: AsyncSession, name: str) -> GenBusiness | None:
        """
        通过 name 获取代码生成业务

        :param db: 数据库会话
        :param name: 表名
        :return:
        """
        return self._single_business(await self.select_model_by_column(db, table_name=name))

    async def get_all(self, db: AsyncSession) -> Sequence[GenBusiness]:
        """
        获取所有代码生成业务

        :param db: 数据库会话
        :return:
        """
        return self._business_sequence(await self.select_models(db))

    async def get_select(self, table_name: str | None) -> Select:
        """
        获取所有代码生成业务查询表达式

        :param table_name: 业务表名
        :return:
        """
        filters: dict[str, Any] = {}

        if table_name is not None:
            filters['table_name__like'] = f'%{table_name}%'

        return await self.select_order('id', 'desc', **cast(Any, filters))

    async def create(self, db: AsyncSession, obj: CreateGenBusinessParam) -> None:
        """
        创建代码生成业务

        :param db: 数据库会话
        :param obj: 创建代码生成业务参数
        :return:
        """
        await self.create_model(db, obj)

    async def update(self, db: AsyncSession, pk: int, obj: UpdateGenBusinessParam) -> int:
        """
        更新代码生成业务

        :param db: 数据库会话
        :param pk: 代码生成业务 ID
        :param obj: 更新代码生成业务参数
        :return:
        """
        return await self.update_model(db, pk, obj)

    async def update_app_name(self, db: AsyncSession, pk: int, app_name: str) -> int:
        """仅更新业务元数据所属应用，保留其余生成配置。"""
        return await self.update_model(db, pk, {'app_name': app_name})

    async def delete(self, db: AsyncSession, pk: int) -> int:
        """
        删除代码生成业务

        :param db: 数据库会话
        :param pk: 代码生成业务 ID
        :return:
        """
        return await self.delete_model(db, pk)


gen_business_dao: CRUDGenBusiness = CRUDGenBusiness(GenBusiness)
