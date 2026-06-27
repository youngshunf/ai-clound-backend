from collections.abc import Sequence
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.hasn_design.crud.crud_hasn_design_project import hasn_design_project_dao
from backend.app.hasn_design.model import HasnDesignProject
from backend.app.hasn_design.schema.hasn_design_project import (
    CreateHasnDesignProjectParam,
    DeleteHasnDesignProjectParam,
    UpdateHasnDesignProjectParam,
)
from backend.common.exception import errors
from backend.common.pagination import paging_data


class HasnDesignProjectService:
    @staticmethod
    async def get(*, db: AsyncSession, pk: int) -> HasnDesignProject:
        """
        获取设计项目（矢量设计 design：云端轻登记元数据，源文件本地优先）

        :param db: 数据库会话
        :param pk: 设计项目（矢量设计 design：云端轻登记元数据，源文件本地优先） ID
        :return:
        """
        hasn_design_project = await hasn_design_project_dao.get(db, pk)
        if not hasn_design_project:
            raise errors.NotFoundError(msg='设计项目（矢量设计 design：云端轻登记元数据，源文件本地优先）不存在')
        return hasn_design_project

    @staticmethod
    async def get_list(db: AsyncSession) -> dict[str, Any]:
        """
        获取设计项目（矢量设计 design：云端轻登记元数据，源文件本地优先）列表

        :param db: 数据库会话
        :return:
        """
        hasn_design_project_select = await hasn_design_project_dao.get_select()
        return await paging_data(db, hasn_design_project_select)

    @staticmethod
    async def get_all(*, db: AsyncSession) -> Sequence[HasnDesignProject]:
        """
        获取所有设计项目（矢量设计 design：云端轻登记元数据，源文件本地优先）

        :param db: 数据库会话
        :return:
        """
        hasn_design_project_list = await hasn_design_project_dao.get_all(db)
        return hasn_design_project_list

    @staticmethod
    async def create(*, db: AsyncSession, obj: CreateHasnDesignProjectParam) -> None:
        """
        创建设计项目（矢量设计 design：云端轻登记元数据，源文件本地优先）

        :param db: 数据库会话
        :param obj: 创建设计项目（矢量设计 design：云端轻登记元数据，源文件本地优先）参数
        :return:
        """
        await hasn_design_project_dao.create(db, obj)

    @staticmethod
    async def update(*, db: AsyncSession, pk: int, obj: UpdateHasnDesignProjectParam) -> int:
        """
        更新设计项目（矢量设计 design：云端轻登记元数据，源文件本地优先）

        :param db: 数据库会话
        :param pk: 设计项目（矢量设计 design：云端轻登记元数据，源文件本地优先） ID
        :param obj: 更新设计项目（矢量设计 design：云端轻登记元数据，源文件本地优先）参数
        :return:
        """
        count = await hasn_design_project_dao.update(db, pk, obj)
        return count

    @staticmethod
    async def delete(*, db: AsyncSession, obj: DeleteHasnDesignProjectParam) -> int:
        """
        删除设计项目（矢量设计 design：云端轻登记元数据，源文件本地优先）

        :param db: 数据库会话
        :param obj: 设计项目（矢量设计 design：云端轻登记元数据，源文件本地优先） ID 列表
        :return:
        """
        count = await hasn_design_project_dao.delete(db, obj.pks)
        return count


hasn_design_project_service: HasnDesignProjectService = HasnDesignProjectService()
