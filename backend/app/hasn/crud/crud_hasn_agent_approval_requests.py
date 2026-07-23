from collections.abc import Sequence
from typing import cast

from sqlalchemy import Select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy_crud_plus import CRUDPlus

from backend.app.hasn.model import HasnAgentApprovalRequests
from backend.app.hasn.schema.hasn_agent_approval_requests import (
    CreateHasnAgentApprovalRequestsParam,
    UpdateHasnAgentApprovalRequestsParam,
)


class CRUDHasnAgentApprovalRequests(CRUDPlus[HasnAgentApprovalRequests]):
    @staticmethod
    def _single_approval_request(result: object) -> HasnAgentApprovalRequests | None:
        """将无关联加载的查询结果收紧为单个审批请求。"""
        if result is not None and not isinstance(result, HasnAgentApprovalRequests):
            raise TypeError('审批请求单模型查询返回了关联结果')
        return cast(HasnAgentApprovalRequests | None, result)

    @staticmethod
    def _approval_request_sequence(result: Sequence[object]) -> Sequence[HasnAgentApprovalRequests]:
        """将无关联加载的查询结果收紧为审批请求序列。"""
        if not all(isinstance(item, HasnAgentApprovalRequests) for item in result):
            raise TypeError('审批请求列表查询返回了关联结果')
        return cast(Sequence[HasnAgentApprovalRequests], result)

    async def get(self, db: AsyncSession, pk: int) -> HasnAgentApprovalRequests | None:
        """
        获取HASN Agent 工具调用审批请求表（A 类 MCP 工具令牌重试）

        :param db: 数据库会话
        :param pk: HASN Agent 工具调用审批请求表（A 类 MCP 工具令牌重试） ID
        :return:
        """
        return self._single_approval_request(await self.select_model(db, pk))

    async def get_by_request_id(self, db: AsyncSession, request_id: str) -> HasnAgentApprovalRequests | None:
        """按业务 request_id 取审批请求行（审批换票/审计主路径）。"""
        return self._single_approval_request(await self.select_model_by_column(db, request_id=request_id))

    async def list_pending_by_agent(self, db: AsyncSession, agent_hasn_id: str) -> Sequence[HasnAgentApprovalRequests]:
        """列出某 Agent 当前挂起（pending）的审批请求（主人 UI / 审计排障）。"""
        return self._approval_request_sequence(
            await self.select_models(db, agent_hasn_id=agent_hasn_id, status='pending')
        )

    async def get_select(self) -> Select:
        """获取HASN Agent 工具调用审批请求表（A 类 MCP 工具令牌重试）列表查询表达式"""
        return await self.select_order('id', 'desc')

    async def get_all(self, db: AsyncSession) -> Sequence[HasnAgentApprovalRequests]:
        """
        获取所有HASN Agent 工具调用审批请求表（A 类 MCP 工具令牌重试）

        :param db: 数据库会话
        :return:
        """
        return self._approval_request_sequence(await self.select_models(db))

    async def create(self, db: AsyncSession, obj: CreateHasnAgentApprovalRequestsParam) -> None:
        """
        创建HASN Agent 工具调用审批请求表（A 类 MCP 工具令牌重试）

        :param db: 数据库会话
        :param obj: 创建HASN Agent 工具调用审批请求表（A 类 MCP 工具令牌重试）参数
        :return:
        """
        await self.create_model(db, obj)

    async def update(self, db: AsyncSession, pk: int, obj: UpdateHasnAgentApprovalRequestsParam) -> int:
        """
        更新HASN Agent 工具调用审批请求表（A 类 MCP 工具令牌重试）

        :param db: 数据库会话
        :param pk: HASN Agent 工具调用审批请求表（A 类 MCP 工具令牌重试） ID
        :param obj: 更新 HASN Agent 工具调用审批请求表（A 类 MCP 工具令牌重试）参数
        :return:
        """
        return await self.update_model(db, pk, obj)

    async def delete(self, db: AsyncSession, pks: list[int]) -> int:
        """
        批量删除HASN Agent 工具调用审批请求表（A 类 MCP 工具令牌重试）

        :param db: 数据库会话
        :param pks: HASN Agent 工具调用审批请求表（A 类 MCP 工具令牌重试） ID 列表
        :return:
        """
        return await self.delete_model_by_column(db, allow_multiple=True, id__in=pks)


hasn_agent_approval_requests_dao: CRUDHasnAgentApprovalRequests = CRUDHasnAgentApprovalRequests(HasnAgentApprovalRequests)
