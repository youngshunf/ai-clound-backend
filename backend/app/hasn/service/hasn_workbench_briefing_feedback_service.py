from typing import Any, Sequence

from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.hasn.crud.crud_hasn_workbench_briefing_feedback import hasn_workbench_briefing_feedback_dao
from backend.app.hasn.model import HasnWorkbenchBriefingFeedback
from backend.app.hasn.schema.hasn_workbench_briefing_feedback import CreateHasnWorkbenchBriefingFeedbackParam, DeleteHasnWorkbenchBriefingFeedbackParam, UpdateHasnWorkbenchBriefingFeedbackParam
from backend.common.exception import errors
from backend.common.pagination import paging_data


class HasnWorkbenchBriefingFeedbackService:
    @staticmethod
    async def record(
        *,
        db: AsyncSession,
        owner_hasn_id: str,
        period: str,
        item_id: str,
        action: str = 'dismiss',
        source_ref: str | None = None,
        note: str | None = None,
    ) -> HasnWorkbenchBriefingFeedback:
        """记一条简报反馈（dismiss/done）。owner 隔离，喂下次简报去重/降权。"""
        obj = CreateHasnWorkbenchBriefingFeedbackParam(
            owner_hasn_id=owner_hasn_id,
            period=period,
            item_id=item_id,
            action=action,
            source_ref=source_ref,
            note=note,
        )
        row = HasnWorkbenchBriefingFeedback(**obj.model_dump())
        db.add(row)
        await db.flush()
        return row

    @staticmethod
    async def get(*, db: AsyncSession, pk: int) -> HasnWorkbenchBriefingFeedback:
        """
        获取HASN 工作台简报反馈（云端权威）

        :param db: 数据库会话
        :param pk: HASN 工作台简报反馈（云端权威） ID
        :return:
        """
        hasn_workbench_briefing_feedback = await hasn_workbench_briefing_feedback_dao.get(db, pk)
        if not hasn_workbench_briefing_feedback:
            raise errors.NotFoundError(msg='HASN 工作台简报反馈（云端权威）不存在')
        return hasn_workbench_briefing_feedback

    @staticmethod
    async def get_list(db: AsyncSession) -> dict[str, Any]:
        """
        获取HASN 工作台简报反馈（云端权威）列表

        :param db: 数据库会话
        :return:
        """
        hasn_workbench_briefing_feedback_select = await hasn_workbench_briefing_feedback_dao.get_select()
        return await paging_data(db, hasn_workbench_briefing_feedback_select)

    @staticmethod
    async def get_all(*, db: AsyncSession) -> Sequence[HasnWorkbenchBriefingFeedback]:
        """
        获取所有HASN 工作台简报反馈（云端权威）

        :param db: 数据库会话
        :return:
        """
        hasn_workbench_briefing_feedback_list = await hasn_workbench_briefing_feedback_dao.get_all(db)
        return hasn_workbench_briefing_feedback_list

    @staticmethod
    async def create(*, db: AsyncSession, obj: CreateHasnWorkbenchBriefingFeedbackParam) -> None:
        """
        创建HASN 工作台简报反馈（云端权威）

        :param db: 数据库会话
        :param obj: 创建HASN 工作台简报反馈（云端权威）参数
        :return:
        """
        await hasn_workbench_briefing_feedback_dao.create(db, obj)

    @staticmethod
    async def update(*, db: AsyncSession, pk: int, obj: UpdateHasnWorkbenchBriefingFeedbackParam) -> int:
        """
        更新HASN 工作台简报反馈（云端权威）

        :param db: 数据库会话
        :param pk: HASN 工作台简报反馈（云端权威） ID
        :param obj: 更新HASN 工作台简报反馈（云端权威）参数
        :return:
        """
        count = await hasn_workbench_briefing_feedback_dao.update(db, pk, obj)
        return count

    @staticmethod
    async def delete(*, db: AsyncSession, obj: DeleteHasnWorkbenchBriefingFeedbackParam) -> int:
        """
        删除HASN 工作台简报反馈（云端权威）

        :param db: 数据库会话
        :param obj: HASN 工作台简报反馈（云端权威） ID 列表
        :return:
        """
        count = await hasn_workbench_briefing_feedback_dao.delete(db, obj.pks)
        return count


hasn_workbench_briefing_feedback_service: HasnWorkbenchBriefingFeedbackService = HasnWorkbenchBriefingFeedbackService()
