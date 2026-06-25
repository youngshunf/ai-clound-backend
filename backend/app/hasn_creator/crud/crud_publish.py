from collections.abc import Sequence

from sqlalchemy import Select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy_crud_plus import CRUDPlus

from backend.app.hasn_creator.model import Publish
from backend.app.hasn_creator.schema.publish import CreatePublishParam, UpdatePublishParam


class CRUDPublish(CRUDPlus[Publish]):
    async def get(self, db: AsyncSession, pk: int) -> Publish | None:
        """
        获取发布记录（= content × account：发到某平台账号 + 数据指标）

        :param db: 数据库会话
        :param pk: 发布记录（= content × account：发到某平台账号 + 数据指标） ID
        :return:
        """
        return await self.select_model(db, pk)

    async def get_select(self) -> Select:
        """获取发布记录（= content × account：发到某平台账号 + 数据指标）列表查询表达式"""
        return await self.select_order('id', 'desc')

    async def get_all(self, db: AsyncSession) -> Sequence[Publish]:
        """
        获取所有发布记录（= content × account：发到某平台账号 + 数据指标）

        :param db: 数据库会话
        :return:
        """
        return await self.select_models(db)

    async def create(self, db: AsyncSession, obj: CreatePublishParam) -> None:
        """
        创建发布记录（= content × account：发到某平台账号 + 数据指标）

        :param db: 数据库会话
        :param obj: 创建发布记录（= content × account：发到某平台账号 + 数据指标）参数
        :return:
        """
        await self.create_model(db, obj)

    async def update(self, db: AsyncSession, pk: int, obj: UpdatePublishParam) -> int:
        """
        更新发布记录（= content × account：发到某平台账号 + 数据指标）

        :param db: 数据库会话
        :param pk: 发布记录（= content × account：发到某平台账号 + 数据指标） ID
        :param obj: 更新 发布记录（= content × account：发到某平台账号 + 数据指标）参数
        :return:
        """
        return await self.update_model(db, pk, obj)

    async def delete(self, db: AsyncSession, pks: list[int]) -> int:
        """
        批量删除发布记录（= content × account：发到某平台账号 + 数据指标）

        :param db: 数据库会话
        :param pks: 发布记录（= content × account：发到某平台账号 + 数据指标） ID 列表
        :return:
        """
        return await self.delete_model_by_column(db, allow_multiple=True, id__in=pks)


publish_dao: CRUDPublish = CRUDPublish(Publish)
