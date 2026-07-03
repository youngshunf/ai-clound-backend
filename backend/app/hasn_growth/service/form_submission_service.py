from collections.abc import Sequence
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.hasn_growth.crud.crud_form_submission import form_submission_dao
from backend.app.hasn_growth.model import FormSubmission
from backend.app.hasn_growth.schema.form_submission import (
    CreateFormSubmissionParam,
    DeleteFormSubmissionParam,
    UpdateFormSubmissionParam,
)
from backend.common.exception import errors
from backend.common.pagination import paging_data


class FormSubmissionService:
    @staticmethod
    async def get(*, db: AsyncSession, pk: int) -> FormSubmission:
        """
        获取获客落地页表单回流（inbound 线索缓冲区）

        :param db: 数据库会话
        :param pk: 获客落地页表单回流（inbound 线索缓冲区） ID
        :return:
        """
        form_submission = await form_submission_dao.get(db, pk)
        if not form_submission:
            raise errors.NotFoundError(msg='获客落地页表单回流（inbound 线索缓冲区）不存在')
        return form_submission

    @staticmethod
    async def get_list(db: AsyncSession) -> dict[str, Any]:
        """
        获取获客落地页表单回流（inbound 线索缓冲区）列表

        :param db: 数据库会话
        :return:
        """
        form_submission_select = await form_submission_dao.get_select()
        return await paging_data(db, form_submission_select)

    @staticmethod
    async def get_all(*, db: AsyncSession) -> Sequence[FormSubmission]:
        """
        获取所有获客落地页表单回流（inbound 线索缓冲区）

        :param db: 数据库会话
        :return:
        """
        form_submission_list = await form_submission_dao.get_all(db)
        return form_submission_list

    @staticmethod
    async def create(*, db: AsyncSession, obj: CreateFormSubmissionParam) -> None:
        """
        创建获客落地页表单回流（inbound 线索缓冲区）

        :param db: 数据库会话
        :param obj: 创建获客落地页表单回流（inbound 线索缓冲区）参数
        :return:
        """
        await form_submission_dao.create(db, obj)

    @staticmethod
    async def update(*, db: AsyncSession, pk: int, obj: UpdateFormSubmissionParam) -> int:
        """
        更新获客落地页表单回流（inbound 线索缓冲区）

        :param db: 数据库会话
        :param pk: 获客落地页表单回流（inbound 线索缓冲区） ID
        :param obj: 更新获客落地页表单回流（inbound 线索缓冲区）参数
        :return:
        """
        count = await form_submission_dao.update(db, pk, obj)
        return count

    @staticmethod
    async def delete(*, db: AsyncSession, obj: DeleteFormSubmissionParam) -> int:
        """
        删除获客落地页表单回流（inbound 线索缓冲区）

        :param db: 数据库会话
        :param obj: 获客落地页表单回流（inbound 线索缓冲区） ID 列表
        :return:
        """
        count = await form_submission_dao.delete(db, obj.pks)
        return count


form_submission_service: FormSubmissionService = FormSubmissionService()
