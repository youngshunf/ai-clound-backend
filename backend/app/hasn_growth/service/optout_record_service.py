from collections.abc import Sequence
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.hasn_growth.crud.crud_optout_record import optout_record_dao
from backend.app.hasn_growth.model import OptoutRecord
from backend.app.hasn_growth.schema.optout_record import (
    CreateOptoutRecordParam,
    DeleteOptoutRecordParam,
    UpdateOptoutRecordParam,
)
from backend.common.exception import errors
from backend.common.pagination import paging_data


class OptoutRecordService:
    @staticmethod
    async def get(*, db: AsyncSession, pk: int) -> OptoutRecord:
        """
        获取获客退订/勿扰登记（合规硬约束，outreach.send 入口硬查，命中即 blocked_optout）

        :param db: 数据库会话
        :param pk: 获客退订/勿扰登记（合规硬约束，outreach.send 入口硬查，命中即 blocked_optout） ID
        :return:
        """
        optout_record = await optout_record_dao.get(db, pk)
        if not optout_record:
            raise errors.NotFoundError(
                msg='获客退订/勿扰登记（合规硬约束，outreach.send 入口硬查，命中即 blocked_optout）不存在'
            )
        return optout_record

    @staticmethod
    async def get_list(db: AsyncSession) -> dict[str, Any]:
        """
        获取获客退订/勿扰登记（合规硬约束，outreach.send 入口硬查，命中即 blocked_optout）列表

        :param db: 数据库会话
        :return:
        """
        optout_record_select = await optout_record_dao.get_select()
        return await paging_data(db, optout_record_select)

    @staticmethod
    async def get_all(*, db: AsyncSession) -> Sequence[OptoutRecord]:
        """
        获取所有获客退订/勿扰登记（合规硬约束，outreach.send 入口硬查，命中即 blocked_optout）

        :param db: 数据库会话
        :return:
        """
        optout_record_list = await optout_record_dao.get_all(db)
        return optout_record_list

    @staticmethod
    async def create(*, db: AsyncSession, obj: CreateOptoutRecordParam) -> None:
        """
        创建获客退订/勿扰登记（合规硬约束，outreach.send 入口硬查，命中即 blocked_optout）

        :param db: 数据库会话
        :param obj: 创建获客退订/勿扰登记（合规硬约束，outreach.send 入口硬查，命中即 blocked_optout）参数
        :return:
        """
        raise errors.ForbiddenError(
            msg='管理端退订写入口已停用，请使用 Owner 退订业务端点',
            data={'error_code': 'GROWTH_OPTOUT_ADMIN_WRITE_RETIRED'},
        )

    @staticmethod
    async def update(*, db: AsyncSession, pk: int, obj: UpdateOptoutRecordParam) -> int:
        """
        更新获客退订/勿扰登记（合规硬约束，outreach.send 入口硬查，命中即 blocked_optout）

        :param db: 数据库会话
        :param pk: 获客退订/勿扰登记（合规硬约束，outreach.send 入口硬查，命中即 blocked_optout） ID
        :param obj: 更新获客退订/勿扰登记（合规硬约束，outreach.send 入口硬查，命中即 blocked_optout）参数
        :return:
        """
        raise errors.ForbiddenError(
            msg='退订记录只允许追加，不允许改写',
            data={'error_code': 'GROWTH_OPTOUT_APPEND_ONLY'},
        )

    @staticmethod
    async def delete(*, db: AsyncSession, obj: DeleteOptoutRecordParam) -> int:
        """
        删除获客退订/勿扰登记（合规硬约束，outreach.send 入口硬查，命中即 blocked_optout）

        :param db: 数据库会话
        :param obj: 获客退订/勿扰登记（合规硬约束，outreach.send 入口硬查，命中即 blocked_optout） ID 列表
        :return:
        """
        raise errors.ForbiddenError(
            msg='退订记录只允许追加，不允许删除',
            data={'error_code': 'GROWTH_OPTOUT_APPEND_ONLY'},
        )


optout_record_service: OptoutRecordService = OptoutRecordService()
