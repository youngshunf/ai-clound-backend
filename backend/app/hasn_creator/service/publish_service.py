from collections.abc import Sequence
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.hasn_creator.crud.crud_publish import publish_dao
from backend.app.hasn_creator.model import Publish
from backend.app.hasn_creator.schema.publish import CreatePublishParam, DeletePublishParam, UpdatePublishParam
from backend.common.exception import errors
from backend.common.pagination import paging_data


class PublishService:
    @staticmethod
    async def get(*, db: AsyncSession, pk: int) -> Publish:
        """
        获取发布记录（= content × account：发到某平台账号 + 数据指标）

        :param db: 数据库会话
        :param pk: 发布记录（= content × account：发到某平台账号 + 数据指标） ID
        :return:
        """
        publish = await publish_dao.get(db, pk)
        if not publish:
            raise errors.NotFoundError(msg='发布记录（= content × account：发到某平台账号 + 数据指标）不存在')
        return publish

    @staticmethod
    async def get_list(db: AsyncSession) -> dict[str, Any]:
        """
        获取发布记录（= content × account：发到某平台账号 + 数据指标）列表

        :param db: 数据库会话
        :return:
        """
        publish_select = await publish_dao.get_select()
        return await paging_data(db, publish_select)

    @staticmethod
    async def get_all(*, db: AsyncSession) -> Sequence[Publish]:
        """
        获取所有发布记录（= content × account：发到某平台账号 + 数据指标）

        :param db: 数据库会话
        :return:
        """
        publish_list = await publish_dao.get_all(db)
        return publish_list

    @staticmethod
    async def create(*, db: AsyncSession, obj: CreatePublishParam) -> None:
        """
        创建发布记录（= content × account：发到某平台账号 + 数据指标）

        :param db: 数据库会话
        :param obj: 创建发布记录（= content × account：发到某平台账号 + 数据指标）参数
        :return:
        """
        await publish_dao.create(db, obj)

    @staticmethod
    async def update(*, db: AsyncSession, pk: int, obj: UpdatePublishParam) -> int:
        """
        更新发布记录（= content × account：发到某平台账号 + 数据指标）

        :param db: 数据库会话
        :param pk: 发布记录（= content × account：发到某平台账号 + 数据指标） ID
        :param obj: 更新发布记录（= content × account：发到某平台账号 + 数据指标）参数
        :return:
        """
        count = await publish_dao.update(db, pk, obj)
        return count

    @staticmethod
    async def delete(*, db: AsyncSession, obj: DeletePublishParam) -> int:
        """
        删除发布记录（= content × account：发到某平台账号 + 数据指标）

        :param db: 数据库会话
        :param obj: 发布记录（= content × account：发到某平台账号 + 数据指标） ID 列表
        :return:
        """
        count = await publish_dao.delete(db, obj.pks)
        return count


publish_service: PublishService = PublishService()
