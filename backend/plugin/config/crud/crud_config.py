from collections.abc import Sequence
from typing import Any, cast

from pydantic import BaseModel
from sqlalchemy import Select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy_crud_plus import CRUDPlus

from backend.plugin.config.model import Config
from backend.plugin.config.schema.config import CreateConfigParam, UpdateConfigParam, UpdateConfigsParam


class CRUDConfig(CRUDPlus[Config]):
    """系统参数参数配置数据库操作类"""

    @staticmethod
    def _single_config(result: object) -> Config | None:
        """将不带关联加载的查询结果收紧为单个参数配置。"""
        if result is not None and not isinstance(result, Config):
            raise TypeError('参数配置单模型查询返回了关联结果')
        return cast(Config | None, result)

    @staticmethod
    def _config_sequence(result: object) -> Sequence[Config]:
        """将不带关联加载的查询结果收紧为参数配置序列。"""
        if not isinstance(result, Sequence) or not all(isinstance(item, Config) for item in result):
            raise TypeError('参数配置列表查询返回了关联结果')
        return cast(Sequence[Config], result)

    async def get(self, db: AsyncSession, pk: int) -> Config | None:
        """
        获取参数配置详情

        :param db: 数据库会话
        :param pk: 参数配置 ID
        :return:
        """
        return self._single_config(await self.select_model_by_column(db, id=pk))

    async def get_all(self, db: AsyncSession, type: str | None) -> Sequence[Config]:
        """
        通过键名获取参数配置

        :param db: 数据库会话
        :param type: 参数配置类型
        :return:
        """
        filters: dict[str, Any] = {}

        if type is not None:
            filters['type'] = type

        return self._config_sequence(await self.select_models(db, **cast(Any, filters)))

    async def get_by_key(self, db: AsyncSession, key: str) -> Config | None:
        """
        通过键名获取参数配置

        :param db: 数据库会话
        :param key: 参数配置键名
        :return:
        """
        return self._single_config(await self.select_model_by_column(db, key=key))

    async def get_select(self, name: str | None, type: str | None) -> Select:
        """
        获取参数配置列表查询表达式

        :param name: 参数配置名称
        :param type: 参数配置类型
        :return:
        """
        filters: dict[str, Any] = {}

        if name is not None:
            filters['name__like'] = f'%{name}%'
        if type is not None:
            filters['type__like'] = f'%{type}%'

        return await self.select_order('created_time', 'desc', **cast(Any, filters))

    async def create(self, db: AsyncSession, obj: CreateConfigParam) -> None:
        """
        创建参数配置

        :param db: 数据库会话
        :param obj: 创建参数配置参数
        :return:
        """
        await self.create_model(db, obj)

    async def update(self, db: AsyncSession, pk: int, obj: UpdateConfigParam) -> int:
        """
        更新参数配置

        :param db: 数据库会话
        :param pk: 参数配置 ID
        :param obj: 更新参数配置参数
        :return:
        """
        return await self.update_model(db, pk, obj)

    async def bulk_update(self, db: AsyncSession, objs: list[UpdateConfigsParam]) -> int:
        """
        批量更新参数配置

        :param db: 数据库会话
        :param objs: 批量更新参数配置参数
        :return:
        """
        values: list[BaseModel | dict[str, Any]] = [obj.model_dump() for obj in objs]
        return await self.bulk_update_models(db, values)

    async def delete(self, db: AsyncSession, pks: list[int]) -> int:
        """
        批量删除参数配置

        :param db: 数据库会话
        :param pks: 参数配置 ID 列表
        :return:
        """
        return await self.delete_model_by_column(db, allow_multiple=True, id__in=pks)


config_dao: CRUDConfig = CRUDConfig(Config)
