from typing import Sequence

from sqlalchemy import Select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy_crud_plus import CRUDPlus

from backend.app.hasn.model import HasnJudgeVerdict
from backend.app.hasn.schema.hasn_judge_verdict import CreateHasnJudgeVerdictParam, UpdateHasnJudgeVerdictParam


class CRUDHasnJudgeVerdict(CRUDPlus[HasnJudgeVerdict]):
    async def get(self, db: AsyncSession, pk: int) -> HasnJudgeVerdict | None:
        """
        获取通用LLM裁判判定记录（doc07 §5.3：教师标签+可观测，全kind共表）

        :param db: 数据库会话
        :param pk: 通用LLM裁判判定记录（doc07 §5.3：教师标签+可观测，全kind共表） ID
        :return:
        """
        return await self.select_model(db, pk)

    async def get_select(self) -> Select:
        """获取通用LLM裁判判定记录（doc07 §5.3：教师标签+可观测，全kind共表）列表查询表达式"""
        return await self.select_order('id', 'desc')

    async def get_all(self, db: AsyncSession) -> Sequence[HasnJudgeVerdict]:
        """
        获取所有通用LLM裁判判定记录（doc07 §5.3：教师标签+可观测，全kind共表）

        :param db: 数据库会话
        :return:
        """
        return await self.select_models(db)

    async def create(self, db: AsyncSession, obj: CreateHasnJudgeVerdictParam) -> None:
        """
        创建通用LLM裁判判定记录（doc07 §5.3：教师标签+可观测，全kind共表）

        :param db: 数据库会话
        :param obj: 创建通用LLM裁判判定记录（doc07 §5.3：教师标签+可观测，全kind共表）参数
        :return:
        """
        await self.create_model(db, obj)

    async def update(self, db: AsyncSession, pk: int, obj: UpdateHasnJudgeVerdictParam) -> int:
        """
        更新通用LLM裁判判定记录（doc07 §5.3：教师标签+可观测，全kind共表）

        :param db: 数据库会话
        :param pk: 通用LLM裁判判定记录（doc07 §5.3：教师标签+可观测，全kind共表） ID
        :param obj: 更新 通用LLM裁判判定记录（doc07 §5.3：教师标签+可观测，全kind共表）参数
        :return:
        """
        return await self.update_model(db, pk, obj)

    async def delete(self, db: AsyncSession, pks: list[int]) -> int:
        """
        批量删除通用LLM裁判判定记录（doc07 §5.3：教师标签+可观测，全kind共表）

        :param db: 数据库会话
        :param pks: 通用LLM裁判判定记录（doc07 §5.3：教师标签+可观测，全kind共表） ID 列表
        :return:
        """
        return await self.delete_model_by_column(db, allow_multiple=True, id__in=pks)


hasn_judge_verdict_dao: CRUDHasnJudgeVerdict = CRUDHasnJudgeVerdict(HasnJudgeVerdict)
