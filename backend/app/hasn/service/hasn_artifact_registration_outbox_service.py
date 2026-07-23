from typing import Any, Sequence

from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.hasn.crud.crud_hasn_artifact_registration_outbox import hasn_artifact_registration_outbox_dao
from backend.app.hasn.model import HasnArtifactRegistrationOutbox
from backend.app.hasn.schema.hasn_artifact_registration_outbox import CreateHasnArtifactRegistrationOutboxParam, DeleteHasnArtifactRegistrationOutboxParam, UpdateHasnArtifactRegistrationOutboxParam
from backend.common.exception import errors
from backend.common.pagination import paging_data


class HasnArtifactRegistrationOutboxService:
    @staticmethod
    async def get(*, db: AsyncSession, pk: int) -> HasnArtifactRegistrationOutbox:
        """
        获取Agent 产物登记可靠投递与修复队列

        :param db: 数据库会话
        :param pk: Agent 产物登记可靠投递与修复队列 ID
        :return:
        """
        hasn_artifact_registration_outbox = await hasn_artifact_registration_outbox_dao.get(db, pk)
        if not hasn_artifact_registration_outbox:
            raise errors.NotFoundError(msg='Agent 产物登记可靠投递与修复队列不存在')
        return hasn_artifact_registration_outbox

    @staticmethod
    async def get_list(db: AsyncSession) -> dict[str, Any]:
        """
        获取Agent 产物登记可靠投递与修复队列列表

        :param db: 数据库会话
        :return:
        """
        hasn_artifact_registration_outbox_select = await hasn_artifact_registration_outbox_dao.get_select()
        return await paging_data(db, hasn_artifact_registration_outbox_select)

    @staticmethod
    async def get_all(*, db: AsyncSession) -> Sequence[HasnArtifactRegistrationOutbox]:
        """
        获取所有Agent 产物登记可靠投递与修复队列

        :param db: 数据库会话
        :return:
        """
        hasn_artifact_registration_outbox_list = await hasn_artifact_registration_outbox_dao.get_all(db)
        return hasn_artifact_registration_outbox_list

    @staticmethod
    async def create(*, db: AsyncSession, obj: CreateHasnArtifactRegistrationOutboxParam) -> None:
        """
        创建Agent 产物登记可靠投递与修复队列

        :param db: 数据库会话
        :param obj: 创建Agent 产物登记可靠投递与修复队列参数
        :return:
        """
        await hasn_artifact_registration_outbox_dao.create(db, obj)

    @staticmethod
    async def update(*, db: AsyncSession, pk: int, obj: UpdateHasnArtifactRegistrationOutboxParam) -> int:
        """
        更新Agent 产物登记可靠投递与修复队列

        :param db: 数据库会话
        :param pk: Agent 产物登记可靠投递与修复队列 ID
        :param obj: 更新Agent 产物登记可靠投递与修复队列参数
        :return:
        """
        count = await hasn_artifact_registration_outbox_dao.update(db, pk, obj)
        return count

    @staticmethod
    async def delete(*, db: AsyncSession, obj: DeleteHasnArtifactRegistrationOutboxParam) -> int:
        """
        删除Agent 产物登记可靠投递与修复队列

        :param db: 数据库会话
        :param obj: Agent 产物登记可靠投递与修复队列 ID 列表
        :return:
        """
        count = await hasn_artifact_registration_outbox_dao.delete(db, obj.pks)
        return count


hasn_artifact_registration_outbox_service: HasnArtifactRegistrationOutboxService = HasnArtifactRegistrationOutboxService()
