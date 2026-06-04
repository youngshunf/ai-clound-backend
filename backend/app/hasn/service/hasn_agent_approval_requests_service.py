from typing import Any, Sequence

from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.hasn.crud.crud_hasn_agent_approval_requests import hasn_agent_approval_requests_dao
from backend.app.hasn.model import HasnAgentApprovalRequests
from backend.app.hasn.schema.hasn_agent_approval_requests import CreateHasnAgentApprovalRequestsParam, DeleteHasnAgentApprovalRequestsParam, UpdateHasnAgentApprovalRequestsParam
from backend.common.exception import errors
from backend.common.pagination import paging_data


class HasnAgentApprovalRequestsService:
    @staticmethod
    async def get(*, db: AsyncSession, pk: int) -> HasnAgentApprovalRequests:
        """
        获取HASN Agent 工具调用审批请求表（A 类 MCP 工具令牌重试）

        :param db: 数据库会话
        :param pk: HASN Agent 工具调用审批请求表（A 类 MCP 工具令牌重试） ID
        :return:
        """
        hasn_agent_approval_requests = await hasn_agent_approval_requests_dao.get(db, pk)
        if not hasn_agent_approval_requests:
            raise errors.NotFoundError(msg='HASN Agent 工具调用审批请求表（A 类 MCP 工具令牌重试）不存在')
        return hasn_agent_approval_requests

    @staticmethod
    async def get_list(db: AsyncSession) -> dict[str, Any]:
        """
        获取HASN Agent 工具调用审批请求表（A 类 MCP 工具令牌重试）列表

        :param db: 数据库会话
        :return:
        """
        hasn_agent_approval_requests_select = await hasn_agent_approval_requests_dao.get_select()
        return await paging_data(db, hasn_agent_approval_requests_select)

    @staticmethod
    async def get_all(*, db: AsyncSession) -> Sequence[HasnAgentApprovalRequests]:
        """
        获取所有HASN Agent 工具调用审批请求表（A 类 MCP 工具令牌重试）

        :param db: 数据库会话
        :return:
        """
        hasn_agent_approval_requests_list = await hasn_agent_approval_requests_dao.get_all(db)
        return hasn_agent_approval_requests_list

    @staticmethod
    async def create(*, db: AsyncSession, obj: CreateHasnAgentApprovalRequestsParam) -> None:
        """
        创建HASN Agent 工具调用审批请求表（A 类 MCP 工具令牌重试）

        :param db: 数据库会话
        :param obj: 创建HASN Agent 工具调用审批请求表（A 类 MCP 工具令牌重试）参数
        :return:
        """
        await hasn_agent_approval_requests_dao.create(db, obj)

    @staticmethod
    async def update(*, db: AsyncSession, pk: int, obj: UpdateHasnAgentApprovalRequestsParam) -> int:
        """
        更新HASN Agent 工具调用审批请求表（A 类 MCP 工具令牌重试）

        :param db: 数据库会话
        :param pk: HASN Agent 工具调用审批请求表（A 类 MCP 工具令牌重试） ID
        :param obj: 更新HASN Agent 工具调用审批请求表（A 类 MCP 工具令牌重试）参数
        :return:
        """
        count = await hasn_agent_approval_requests_dao.update(db, pk, obj)
        return count

    @staticmethod
    async def delete(*, db: AsyncSession, obj: DeleteHasnAgentApprovalRequestsParam) -> int:
        """
        删除HASN Agent 工具调用审批请求表（A 类 MCP 工具令牌重试）

        :param db: 数据库会话
        :param obj: HASN Agent 工具调用审批请求表（A 类 MCP 工具令牌重试） ID 列表
        :return:
        """
        count = await hasn_agent_approval_requests_dao.delete(db, obj.pks)
        return count


hasn_agent_approval_requests_service: HasnAgentApprovalRequestsService = HasnAgentApprovalRequestsService()
