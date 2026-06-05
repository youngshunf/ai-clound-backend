import uuid

from typing import Any, Sequence

from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.hasn.crud.crud_hasn_workbench_briefing import hasn_workbench_briefing_dao
from backend.app.hasn.model import HasnWorkbenchBriefing
from backend.app.hasn.schema.hasn_workbench_briefing import CreateHasnWorkbenchBriefingParam, DeleteHasnWorkbenchBriefingParam, UpdateHasnWorkbenchBriefingParam
from backend.app.hasn.schema.workbench_briefing_document import BriefingDocument
from backend.common.exception import errors
from backend.common.pagination import paging_data
from backend.utils.timezone import timezone


class HasnWorkbenchBriefingService:
    @staticmethod
    async def publish(
        *, db: AsyncSession, owner_hasn_id: str, agent_hasn_id: str, document: dict[str, Any]
    ) -> HasnWorkbenchBriefing:
        """主脑发布一份 BriefingDocument（覆盖当日 period）。

        **强校验** document 合 BriefingDocument schema（不合 raise ValidationError，工具入口
        让模型重试——零 fake，绝不正则解析自由文本）。owner_id/agent_id 由凭证回填（身份由认证
        决定，不信任入参），period 取文档值或当日。
        """
        # 校验失败抛 pydantic ValidationError，由调用方（MCP 工具）转成重试信号。
        doc = BriefingDocument.model_validate(document)
        period = doc.period or timezone.now().strftime('%Y-%m-%d')
        stored = doc.model_dump(mode='json')
        # 身份由认证回填（绝不信任文档自带的 owner_id/agent_id）。
        stored['owner_id'] = owner_hasn_id
        stored['agent_id'] = agent_hasn_id
        stored['period'] = period
        if not stored.get('briefing_id'):
            stored['briefing_id'] = f'brf_{uuid.uuid4().hex}'
        if not stored.get('generated_at'):
            stored['generated_at'] = int(timezone.now().timestamp() * 1000)
        return await hasn_workbench_briefing_dao.upsert_by_owner_period(
            db,
            owner_hasn_id=owner_hasn_id,
            agent_hasn_id=agent_hasn_id,
            period=period,
            state=doc.state,
            document_json=stored,
        )

    @staticmethod
    async def get_latest(
        *, db: AsyncSession, owner_hasn_id: str, period: str | None = None
    ) -> HasnWorkbenchBriefing | None:
        """取某 owner 的最新简报（owner 隔离）。无则返回 None（空态如实，不造卡）。"""
        return await hasn_workbench_briefing_dao.get_latest_by_owner(db, owner_hasn_id, period)

    @staticmethod
    async def get(*, db: AsyncSession, pk: int) -> HasnWorkbenchBriefing:
        """
        获取HASN 工作台每日关注简报（云端权威）

        :param db: 数据库会话
        :param pk: HASN 工作台每日关注简报（云端权威） ID
        :return:
        """
        hasn_workbench_briefing = await hasn_workbench_briefing_dao.get(db, pk)
        if not hasn_workbench_briefing:
            raise errors.NotFoundError(msg='HASN 工作台每日关注简报（云端权威）不存在')
        return hasn_workbench_briefing

    @staticmethod
    async def get_list(db: AsyncSession) -> dict[str, Any]:
        """
        获取HASN 工作台每日关注简报（云端权威）列表

        :param db: 数据库会话
        :return:
        """
        hasn_workbench_briefing_select = await hasn_workbench_briefing_dao.get_select()
        return await paging_data(db, hasn_workbench_briefing_select)

    @staticmethod
    async def get_all(*, db: AsyncSession) -> Sequence[HasnWorkbenchBriefing]:
        """
        获取所有HASN 工作台每日关注简报（云端权威）

        :param db: 数据库会话
        :return:
        """
        hasn_workbench_briefing_list = await hasn_workbench_briefing_dao.get_all(db)
        return hasn_workbench_briefing_list

    @staticmethod
    async def create(*, db: AsyncSession, obj: CreateHasnWorkbenchBriefingParam) -> None:
        """
        创建HASN 工作台每日关注简报（云端权威）

        :param db: 数据库会话
        :param obj: 创建HASN 工作台每日关注简报（云端权威）参数
        :return:
        """
        await hasn_workbench_briefing_dao.create(db, obj)

    @staticmethod
    async def update(*, db: AsyncSession, pk: int, obj: UpdateHasnWorkbenchBriefingParam) -> int:
        """
        更新HASN 工作台每日关注简报（云端权威）

        :param db: 数据库会话
        :param pk: HASN 工作台每日关注简报（云端权威） ID
        :param obj: 更新HASN 工作台每日关注简报（云端权威）参数
        :return:
        """
        count = await hasn_workbench_briefing_dao.update(db, pk, obj)
        return count

    @staticmethod
    async def delete(*, db: AsyncSession, obj: DeleteHasnWorkbenchBriefingParam) -> int:
        """
        删除HASN 工作台每日关注简报（云端权威）

        :param db: 数据库会话
        :param obj: HASN 工作台每日关注简报（云端权威） ID 列表
        :return:
        """
        count = await hasn_workbench_briefing_dao.delete(db, obj.pks)
        return count


hasn_workbench_briefing_service: HasnWorkbenchBriefingService = HasnWorkbenchBriefingService()
