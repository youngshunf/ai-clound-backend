from collections.abc import Sequence
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.hasn.crud.crud_hasn_session_artifacts import hasn_session_artifacts_dao
from backend.app.hasn.model import HasnSessionArtifacts
from backend.app.hasn.schema.hasn_session_artifacts import (
    CreateHasnSessionArtifactsParam,
    DeleteHasnSessionArtifactsParam,
    UpdateHasnSessionArtifactsParam,
)
from backend.common.exception import errors
from backend.common.pagination import paging_data


class HasnSessionArtifactsService:
    @staticmethod
    async def get(*, db: AsyncSession, pk: int) -> HasnSessionArtifacts:
        """
        获取HASN 会话产物

        :param db: 数据库会话
        :param pk: HASN 会话产物 ID
        :return:
        """
        hasn_session_artifacts = await hasn_session_artifacts_dao.get(db, pk)
        if not hasn_session_artifacts:
            raise errors.NotFoundError(msg='HASN 会话产物不存在')
        return hasn_session_artifacts

    @staticmethod
    async def get_list(db: AsyncSession) -> dict[str, Any]:
        """
        获取HASN 会话产物列表

        :param db: 数据库会话
        :return:
        """
        hasn_session_artifacts_select = await hasn_session_artifacts_dao.get_select()
        return await paging_data(db, hasn_session_artifacts_select)

    @staticmethod
    async def get_all(*, db: AsyncSession) -> Sequence[HasnSessionArtifacts]:
        """
        获取所有HASN 会话产物

        :param db: 数据库会话
        :return:
        """
        hasn_session_artifacts_list = await hasn_session_artifacts_dao.get_all(db)
        return hasn_session_artifacts_list

    @staticmethod
    async def create(*, db: AsyncSession, obj: CreateHasnSessionArtifactsParam) -> None:
        """
        创建HASN 会话产物

        :param db: 数据库会话
        :param obj: 创建HASN 会话产物参数
        :return:
        """
        await hasn_session_artifacts_dao.create(db, obj)

    @staticmethod
    async def update(*, db: AsyncSession, pk: int, obj: UpdateHasnSessionArtifactsParam) -> int:
        """
        更新HASN 会话产物

        :param db: 数据库会话
        :param pk: HASN 会话产物 ID
        :param obj: 更新HASN 会话产物参数
        :return:
        """
        count = await hasn_session_artifacts_dao.update(db, pk, obj)
        return count

    @staticmethod
    async def delete(*, db: AsyncSession, obj: DeleteHasnSessionArtifactsParam) -> int:
        """
        删除HASN 会话产物

        :param db: 数据库会话
        :param obj: HASN 会话产物 ID 列表
        :return:
        """
        count = await hasn_session_artifacts_dao.delete(db, obj.pks)
        return count


hasn_session_artifacts_service: HasnSessionArtifactsService = HasnSessionArtifactsService()
