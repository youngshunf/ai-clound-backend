from typing import Any, Sequence

from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.hasn_designsystem.crud.crud_revision import revision_dao
from backend.app.hasn_designsystem.model import Revision
from backend.app.hasn_designsystem.schema.revision import CreateRevisionParam, DeleteRevisionParam, UpdateRevisionParam
from backend.common.exception import errors
from backend.common.pagination import paging_data


class RevisionService:
    @staticmethod
    async def get(*, db: AsyncSession, pk: int) -> Revision:
        """
        获取演示文稿版本快照（云端权威历史）

        :param db: 数据库会话
        :param pk: 演示文稿版本快照（云端权威历史） ID
        :return:
        """
        revision = await revision_dao.get(db, pk)
        if not revision:
            raise errors.NotFoundError(msg='演示文稿版本快照（云端权威历史）不存在')
        return revision

    @staticmethod
    async def get_list(db: AsyncSession) -> dict[str, Any]:
        """
        获取演示文稿版本快照（云端权威历史）列表

        :param db: 数据库会话
        :return:
        """
        revision_select = await revision_dao.get_select()
        return await paging_data(db, revision_select)

    @staticmethod
    async def get_all(*, db: AsyncSession) -> Sequence[Revision]:
        """
        获取所有演示文稿版本快照（云端权威历史）

        :param db: 数据库会话
        :return:
        """
        revision_list = await revision_dao.get_all(db)
        return revision_list

    @staticmethod
    async def create(*, db: AsyncSession, obj: CreateRevisionParam) -> None:
        """
        创建演示文稿版本快照（云端权威历史）

        :param db: 数据库会话
        :param obj: 创建演示文稿版本快照（云端权威历史）参数
        :return:
        """
        await revision_dao.create(db, obj)

    @staticmethod
    async def update(*, db: AsyncSession, pk: int, obj: UpdateRevisionParam) -> int:
        """
        更新演示文稿版本快照（云端权威历史）

        :param db: 数据库会话
        :param pk: 演示文稿版本快照（云端权威历史） ID
        :param obj: 更新演示文稿版本快照（云端权威历史）参数
        :return:
        """
        count = await revision_dao.update(db, pk, obj)
        return count

    @staticmethod
    async def delete(*, db: AsyncSession, obj: DeleteRevisionParam) -> int:
        """
        删除演示文稿版本快照（云端权威历史）

        :param db: 数据库会话
        :param obj: 演示文稿版本快照（云端权威历史） ID 列表
        :return:
        """
        count = await revision_dao.delete(db, obj.pks)
        return count


revision_service: RevisionService = RevisionService()
