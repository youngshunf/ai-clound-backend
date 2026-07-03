from collections.abc import Sequence
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.hasn_designsystem.crud.crud_collaborator import collaborator_dao
from backend.app.hasn_designsystem.model import Collaborator
from backend.app.hasn_designsystem.schema.collaborator import (
    CreateCollaboratorParam,
    DeleteCollaboratorParam,
    UpdateCollaboratorParam,
)
from backend.common.exception import errors
from backend.common.pagination import paging_data


class CollaboratorService:
    @staticmethod
    async def get(*, db: AsyncSession, pk: int) -> Collaborator:
        """
        获取设计系统协作分身绑定（对齐 DECKBIND）

        :param db: 数据库会话
        :param pk: 设计系统协作分身绑定（对齐 DECKBIND） ID
        :return:
        """
        collaborator = await collaborator_dao.get(db, pk)
        if not collaborator:
            raise errors.NotFoundError(msg='设计系统协作分身绑定（对齐 DECKBIND）不存在')
        return collaborator

    @staticmethod
    async def get_list(db: AsyncSession) -> dict[str, Any]:
        """
        获取设计系统协作分身绑定（对齐 DECKBIND）列表

        :param db: 数据库会话
        :return:
        """
        collaborator_select = await collaborator_dao.get_select()
        return await paging_data(db, collaborator_select)

    @staticmethod
    async def get_all(*, db: AsyncSession) -> Sequence[Collaborator]:
        """
        获取所有设计系统协作分身绑定（对齐 DECKBIND）

        :param db: 数据库会话
        :return:
        """
        collaborator_list = await collaborator_dao.get_all(db)
        return collaborator_list

    @staticmethod
    async def create(*, db: AsyncSession, obj: CreateCollaboratorParam) -> None:
        """
        创建设计系统协作分身绑定（对齐 DECKBIND）

        :param db: 数据库会话
        :param obj: 创建设计系统协作分身绑定（对齐 DECKBIND）参数
        :return:
        """
        await collaborator_dao.create(db, obj)

    @staticmethod
    async def update(*, db: AsyncSession, pk: int, obj: UpdateCollaboratorParam) -> int:
        """
        更新设计系统协作分身绑定（对齐 DECKBIND）

        :param db: 数据库会话
        :param pk: 设计系统协作分身绑定（对齐 DECKBIND） ID
        :param obj: 更新设计系统协作分身绑定（对齐 DECKBIND）参数
        :return:
        """
        count = await collaborator_dao.update(db, pk, obj)
        return count

    @staticmethod
    async def delete(*, db: AsyncSession, obj: DeleteCollaboratorParam) -> int:
        """
        删除设计系统协作分身绑定（对齐 DECKBIND）

        :param db: 数据库会话
        :param obj: 设计系统协作分身绑定（对齐 DECKBIND） ID 列表
        :return:
        """
        count = await collaborator_dao.delete(db, obj.pks)
        return count


collaborator_service: CollaboratorService = CollaboratorService()
