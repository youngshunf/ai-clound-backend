from typing import Any, Sequence

from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.hasn_growth.crud.crud_playbook import playbook_dao
from backend.app.hasn_growth.model import Playbook
from backend.app.hasn_growth.schema.playbook import CreatePlaybookParam, DeletePlaybookParam, UpdatePlaybookParam
from backend.common.exception import errors
from backend.common.pagination import paging_data


class PlaybookService:
    @staticmethod
    async def get(*, db: AsyncSession, pk: int) -> Playbook:
        """
        获取获客打法模板（目标画像 + 触达节奏 + 话术要点），内置 + 自定义

        :param db: 数据库会话
        :param pk: 获客打法模板（目标画像 + 触达节奏 + 话术要点），内置 + 自定义 ID
        :return:
        """
        playbook = await playbook_dao.get(db, pk)
        if not playbook:
            raise errors.NotFoundError(msg='获客打法模板（目标画像 + 触达节奏 + 话术要点），内置 + 自定义不存在')
        return playbook

    @staticmethod
    async def get_list(db: AsyncSession) -> dict[str, Any]:
        """
        获取获客打法模板（目标画像 + 触达节奏 + 话术要点），内置 + 自定义列表

        :param db: 数据库会话
        :return:
        """
        playbook_select = await playbook_dao.get_select()
        return await paging_data(db, playbook_select)

    @staticmethod
    async def get_all(*, db: AsyncSession) -> Sequence[Playbook]:
        """
        获取所有获客打法模板（目标画像 + 触达节奏 + 话术要点），内置 + 自定义

        :param db: 数据库会话
        :return:
        """
        playbook_list = await playbook_dao.get_all(db)
        return playbook_list

    @staticmethod
    async def create(*, db: AsyncSession, obj: CreatePlaybookParam) -> None:
        """
        创建获客打法模板（目标画像 + 触达节奏 + 话术要点），内置 + 自定义

        :param db: 数据库会话
        :param obj: 创建获客打法模板（目标画像 + 触达节奏 + 话术要点），内置 + 自定义参数
        :return:
        """
        await playbook_dao.create(db, obj)

    @staticmethod
    async def update(*, db: AsyncSession, pk: int, obj: UpdatePlaybookParam) -> int:
        """
        更新获客打法模板（目标画像 + 触达节奏 + 话术要点），内置 + 自定义

        :param db: 数据库会话
        :param pk: 获客打法模板（目标画像 + 触达节奏 + 话术要点），内置 + 自定义 ID
        :param obj: 更新获客打法模板（目标画像 + 触达节奏 + 话术要点），内置 + 自定义参数
        :return:
        """
        count = await playbook_dao.update(db, pk, obj)
        return count

    @staticmethod
    async def delete(*, db: AsyncSession, obj: DeletePlaybookParam) -> int:
        """
        删除获客打法模板（目标画像 + 触达节奏 + 话术要点），内置 + 自定义

        :param db: 数据库会话
        :param obj: 获客打法模板（目标画像 + 触达节奏 + 话术要点），内置 + 自定义 ID 列表
        :return:
        """
        count = await playbook_dao.delete(db, obj.pks)
        return count


playbook_service: PlaybookService = PlaybookService()
