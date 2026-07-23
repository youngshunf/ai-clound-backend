from typing import Any, Sequence

from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.hasn.crud.crud_hasn_artifact_contributions import hasn_artifact_contributions_dao
from backend.app.hasn.model import HasnArtifactContributions
from backend.app.hasn.schema.hasn_artifact_contributions import CreateHasnArtifactContributionsParam, DeleteHasnArtifactContributionsParam, UpdateHasnArtifactContributionsParam
from backend.common.exception import errors
from backend.common.pagination import paging_data


class HasnArtifactContributionsService:
    @staticmethod
    async def get(*, db: AsyncSession, pk: int) -> HasnArtifactContributions:
        """
        获取Agent 对产物的不可变参与记录

        :param db: 数据库会话
        :param pk: Agent 对产物的不可变参与记录 ID
        :return:
        """
        hasn_artifact_contributions = await hasn_artifact_contributions_dao.get(db, pk)
        if not hasn_artifact_contributions:
            raise errors.NotFoundError(msg='Agent 对产物的不可变参与记录不存在')
        return hasn_artifact_contributions

    @staticmethod
    async def get_list(db: AsyncSession) -> dict[str, Any]:
        """
        获取Agent 对产物的不可变参与记录列表

        :param db: 数据库会话
        :return:
        """
        hasn_artifact_contributions_select = await hasn_artifact_contributions_dao.get_select()
        return await paging_data(db, hasn_artifact_contributions_select)

    @staticmethod
    async def get_all(*, db: AsyncSession) -> Sequence[HasnArtifactContributions]:
        """
        获取所有Agent 对产物的不可变参与记录

        :param db: 数据库会话
        :return:
        """
        hasn_artifact_contributions_list = await hasn_artifact_contributions_dao.get_all(db)
        return hasn_artifact_contributions_list

    @staticmethod
    async def create(*, db: AsyncSession, obj: CreateHasnArtifactContributionsParam) -> None:
        """
        创建Agent 对产物的不可变参与记录

        :param db: 数据库会话
        :param obj: 创建Agent 对产物的不可变参与记录参数
        :return:
        """
        await hasn_artifact_contributions_dao.create(db, obj)

    @staticmethod
    async def update(*, db: AsyncSession, pk: int, obj: UpdateHasnArtifactContributionsParam) -> int:
        """
        更新Agent 对产物的不可变参与记录

        :param db: 数据库会话
        :param pk: Agent 对产物的不可变参与记录 ID
        :param obj: 更新Agent 对产物的不可变参与记录参数
        :return:
        """
        count = await hasn_artifact_contributions_dao.update(db, pk, obj)
        return count

    @staticmethod
    async def delete(*, db: AsyncSession, obj: DeleteHasnArtifactContributionsParam) -> int:
        """
        删除Agent 对产物的不可变参与记录

        :param db: 数据库会话
        :param obj: Agent 对产物的不可变参与记录 ID 列表
        :return:
        """
        count = await hasn_artifact_contributions_dao.delete(db, obj.pks)
        return count


hasn_artifact_contributions_service: HasnArtifactContributionsService = HasnArtifactContributionsService()
