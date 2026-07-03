from collections.abc import Sequence
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.hasn_reel.crud.crud_reel_creation import reel_creation_dao
from backend.app.hasn_reel.model import ReelCreation
from backend.app.hasn_reel.schema.reel_creation import (
    CreateReelCreationParam,
    DeleteReelCreationParam,
    UpdateReelCreationParam,
)
from backend.common.exception import errors
from backend.common.pagination import paging_data


class ReelCreationService:
    @staticmethod
    async def get(*, db: AsyncSession, pk: int) -> ReelCreation:
        """
        获取一次短视频创作（分身工作流 + 进度 + 产物 + 历史；引擎/会话权威态经 daemon 同步落库）

        :param db: 数据库会话
        :param pk: 一次短视频创作（分身工作流 + 进度 + 产物 + 历史；引擎/会话权威态经 daemon 同步落库） ID
        :return:
        """
        reel_creation = await reel_creation_dao.get(db, pk)
        if not reel_creation:
            raise errors.NotFoundError(msg='一次短视频创作（分身工作流 + 进度 + 产物 + 历史；引擎/会话权威态经 daemon 同步落库）不存在')
        return reel_creation

    @staticmethod
    async def get_list(db: AsyncSession) -> dict[str, Any]:
        """
        获取一次短视频创作（分身工作流 + 进度 + 产物 + 历史；引擎/会话权威态经 daemon 同步落库）列表

        :param db: 数据库会话
        :return:
        """
        reel_creation_select = await reel_creation_dao.get_select()
        return await paging_data(db, reel_creation_select)

    @staticmethod
    async def get_all(*, db: AsyncSession) -> Sequence[ReelCreation]:
        """
        获取所有一次短视频创作（分身工作流 + 进度 + 产物 + 历史；引擎/会话权威态经 daemon 同步落库）

        :param db: 数据库会话
        :return:
        """
        reel_creation_list = await reel_creation_dao.get_all(db)
        return reel_creation_list

    @staticmethod
    async def create(*, db: AsyncSession, obj: CreateReelCreationParam) -> None:
        """
        创建一次短视频创作（分身工作流 + 进度 + 产物 + 历史；引擎/会话权威态经 daemon 同步落库）

        :param db: 数据库会话
        :param obj: 创建一次短视频创作（分身工作流 + 进度 + 产物 + 历史；引擎/会话权威态经 daemon 同步落库）参数
        :return:
        """
        await reel_creation_dao.create(db, obj)

    @staticmethod
    async def update(*, db: AsyncSession, pk: int, obj: UpdateReelCreationParam) -> int:
        """
        更新一次短视频创作（分身工作流 + 进度 + 产物 + 历史；引擎/会话权威态经 daemon 同步落库）

        :param db: 数据库会话
        :param pk: 一次短视频创作（分身工作流 + 进度 + 产物 + 历史；引擎/会话权威态经 daemon 同步落库） ID
        :param obj: 更新一次短视频创作（分身工作流 + 进度 + 产物 + 历史；引擎/会话权威态经 daemon 同步落库）参数
        :return:
        """
        count = await reel_creation_dao.update(db, pk, obj)
        return count

    @staticmethod
    async def delete(*, db: AsyncSession, obj: DeleteReelCreationParam) -> int:
        """
        删除一次短视频创作（分身工作流 + 进度 + 产物 + 历史；引擎/会话权威态经 daemon 同步落库）

        :param db: 数据库会话
        :param obj: 一次短视频创作（分身工作流 + 进度 + 产物 + 历史；引擎/会话权威态经 daemon 同步落库） ID 列表
        :return:
        """
        count = await reel_creation_dao.delete(db, obj.pks)
        return count


reel_creation_service: ReelCreationService = ReelCreationService()
