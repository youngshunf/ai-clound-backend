from typing import Sequence

from sqlalchemy import Select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy_crud_plus import CRUDPlus

from backend.app.hasn_reel.model import ReelCreation
from backend.app.hasn_reel.schema.reel_creation import CreateReelCreationParam, UpdateReelCreationParam


class CRUDReelCreation(CRUDPlus[ReelCreation]):
    async def get(self, db: AsyncSession, pk: int) -> ReelCreation | None:
        """
        获取一次短视频创作（分身工作流 + 进度 + 产物 + 历史；引擎/会话权威态经 daemon 同步落库）

        :param db: 数据库会话
        :param pk: 一次短视频创作（分身工作流 + 进度 + 产物 + 历史；引擎/会话权威态经 daemon 同步落库） ID
        :return:
        """
        return await self.select_model(db, pk)

    async def get_select(self) -> Select:
        """获取一次短视频创作（分身工作流 + 进度 + 产物 + 历史；引擎/会话权威态经 daemon 同步落库）列表查询表达式"""
        return await self.select_order('id', 'desc')

    async def get_all(self, db: AsyncSession) -> Sequence[ReelCreation]:
        """
        获取所有一次短视频创作（分身工作流 + 进度 + 产物 + 历史；引擎/会话权威态经 daemon 同步落库）

        :param db: 数据库会话
        :return:
        """
        return await self.select_models(db)

    async def create(self, db: AsyncSession, obj: CreateReelCreationParam) -> None:
        """
        创建一次短视频创作（分身工作流 + 进度 + 产物 + 历史；引擎/会话权威态经 daemon 同步落库）

        :param db: 数据库会话
        :param obj: 创建一次短视频创作（分身工作流 + 进度 + 产物 + 历史；引擎/会话权威态经 daemon 同步落库）参数
        :return:
        """
        await self.create_model(db, obj)

    async def update(self, db: AsyncSession, pk: int, obj: UpdateReelCreationParam) -> int:
        """
        更新一次短视频创作（分身工作流 + 进度 + 产物 + 历史；引擎/会话权威态经 daemon 同步落库）

        :param db: 数据库会话
        :param pk: 一次短视频创作（分身工作流 + 进度 + 产物 + 历史；引擎/会话权威态经 daemon 同步落库） ID
        :param obj: 更新 一次短视频创作（分身工作流 + 进度 + 产物 + 历史；引擎/会话权威态经 daemon 同步落库）参数
        :return:
        """
        return await self.update_model(db, pk, obj)

    async def delete(self, db: AsyncSession, pks: list[int]) -> int:
        """
        批量删除一次短视频创作（分身工作流 + 进度 + 产物 + 历史；引擎/会话权威态经 daemon 同步落库）

        :param db: 数据库会话
        :param pks: 一次短视频创作（分身工作流 + 进度 + 产物 + 历史；引擎/会话权威态经 daemon 同步落库） ID 列表
        :return:
        """
        return await self.delete_model_by_column(db, allow_multiple=True, id__in=pks)


reel_creation_dao: CRUDReelCreation = CRUDReelCreation(ReelCreation)
