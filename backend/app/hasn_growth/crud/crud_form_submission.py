from typing import Sequence

from sqlalchemy import Select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy_crud_plus import CRUDPlus

from backend.app.hasn_growth.model import FormSubmission
from backend.app.hasn_growth.schema.form_submission import CreateFormSubmissionParam, UpdateFormSubmissionParam


class CRUDFormSubmission(CRUDPlus[FormSubmission]):
    async def get(self, db: AsyncSession, pk: int) -> FormSubmission | None:
        """
        获取获客落地页表单回流（inbound 线索缓冲区）

        :param db: 数据库会话
        :param pk: 获客落地页表单回流（inbound 线索缓冲区） ID
        :return:
        """
        return await self.select_model(db, pk)

    async def get_select(self) -> Select:
        """获取获客落地页表单回流（inbound 线索缓冲区）列表查询表达式"""
        return await self.select_order('id', 'desc')

    async def get_all(self, db: AsyncSession) -> Sequence[FormSubmission]:
        """
        获取所有获客落地页表单回流（inbound 线索缓冲区）

        :param db: 数据库会话
        :return:
        """
        return await self.select_models(db)

    async def create(self, db: AsyncSession, obj: CreateFormSubmissionParam) -> None:
        """
        创建获客落地页表单回流（inbound 线索缓冲区）

        :param db: 数据库会话
        :param obj: 创建获客落地页表单回流（inbound 线索缓冲区）参数
        :return:
        """
        await self.create_model(db, obj)

    async def update(self, db: AsyncSession, pk: int, obj: UpdateFormSubmissionParam) -> int:
        """
        更新获客落地页表单回流（inbound 线索缓冲区）

        :param db: 数据库会话
        :param pk: 获客落地页表单回流（inbound 线索缓冲区） ID
        :param obj: 更新 获客落地页表单回流（inbound 线索缓冲区）参数
        :return:
        """
        return await self.update_model(db, pk, obj)

    async def delete(self, db: AsyncSession, pks: list[int]) -> int:
        """
        批量删除获客落地页表单回流（inbound 线索缓冲区）

        :param db: 数据库会话
        :param pks: 获客落地页表单回流（inbound 线索缓冲区） ID 列表
        :return:
        """
        return await self.delete_model_by_column(db, allow_multiple=True, id__in=pks)


form_submission_dao: CRUDFormSubmission = CRUDFormSubmission(FormSubmission)
