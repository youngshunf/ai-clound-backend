from collections.abc import Sequence
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.hasn_studio.crud.crud_studio_render_job import studio_render_job_dao
from backend.app.hasn_studio.model import StudioRenderJob
from backend.app.hasn_studio.schema.studio_render_job import (
    CreateStudioRenderJobParam,
    DeleteStudioRenderJobParam,
    UpdateStudioRenderJobParam,
)
from backend.common.exception import errors
from backend.common.pagination import paging_data


class StudioRenderJobService:
    @staticmethod
    async def get(*, db: AsyncSession, pk: int) -> StudioRenderJob:
        """
        获取视频渲染任务（运行态镜像 + 进度/成本 + 审计；引擎权威态经云端轮询/webhook 同步落库）

        :param db: 数据库会话
        :param pk: 视频渲染任务（运行态镜像 + 进度/成本 + 审计；引擎权威态经云端轮询/webhook 同步落库） ID
        :return:
        """
        studio_render_job = await studio_render_job_dao.get(db, pk)
        if not studio_render_job:
            raise errors.NotFoundError(msg='视频渲染任务（运行态镜像 + 进度/成本 + 审计；引擎权威态经云端轮询/webhook 同步落库）不存在')
        return studio_render_job

    @staticmethod
    async def get_list(db: AsyncSession) -> dict[str, Any]:
        """
        获取视频渲染任务（运行态镜像 + 进度/成本 + 审计；引擎权威态经云端轮询/webhook 同步落库）列表

        :param db: 数据库会话
        :return:
        """
        studio_render_job_select = await studio_render_job_dao.get_select()
        return await paging_data(db, studio_render_job_select)

    @staticmethod
    async def get_all(*, db: AsyncSession) -> Sequence[StudioRenderJob]:
        """
        获取所有视频渲染任务（运行态镜像 + 进度/成本 + 审计；引擎权威态经云端轮询/webhook 同步落库）

        :param db: 数据库会话
        :return:
        """
        studio_render_job_list = await studio_render_job_dao.get_all(db)
        return studio_render_job_list

    @staticmethod
    async def create(*, db: AsyncSession, obj: CreateStudioRenderJobParam) -> None:
        """
        创建视频渲染任务（运行态镜像 + 进度/成本 + 审计；引擎权威态经云端轮询/webhook 同步落库）

        :param db: 数据库会话
        :param obj: 创建视频渲染任务（运行态镜像 + 进度/成本 + 审计；引擎权威态经云端轮询/webhook 同步落库）参数
        :return:
        """
        await studio_render_job_dao.create(db, obj)

    @staticmethod
    async def update(*, db: AsyncSession, pk: int, obj: UpdateStudioRenderJobParam) -> int:
        """
        更新视频渲染任务（运行态镜像 + 进度/成本 + 审计；引擎权威态经云端轮询/webhook 同步落库）

        :param db: 数据库会话
        :param pk: 视频渲染任务（运行态镜像 + 进度/成本 + 审计；引擎权威态经云端轮询/webhook 同步落库） ID
        :param obj: 更新视频渲染任务（运行态镜像 + 进度/成本 + 审计；引擎权威态经云端轮询/webhook 同步落库）参数
        :return:
        """
        count = await studio_render_job_dao.update(db, pk, obj)
        return count

    @staticmethod
    async def delete(*, db: AsyncSession, obj: DeleteStudioRenderJobParam) -> int:
        """
        删除视频渲染任务（运行态镜像 + 进度/成本 + 审计；引擎权威态经云端轮询/webhook 同步落库）

        :param db: 数据库会话
        :param obj: 视频渲染任务（运行态镜像 + 进度/成本 + 审计；引擎权威态经云端轮询/webhook 同步落库） ID 列表
        :return:
        """
        count = await studio_render_job_dao.delete(db, obj.pks)
        return count


studio_render_job_service: StudioRenderJobService = StudioRenderJobService()
