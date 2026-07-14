"""工作流数据访问层（hasn_task.workflow / workflow_edge / workflow_run）。

复杂建图（无环校验 + 节点 task + 边）在 service 层；DAO 只提供基础读写访问。
"""

from collections.abc import Sequence

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy_crud_plus import CRUDPlus

from backend.app.hasn_task.model import (
    HasnWorkflow,
    HasnWorkflowEdge,
    HasnWorkflowNode,
    HasnWorkflowNodeRun,
    HasnWorkflowRun,
)


class CRUDHasnWorkflow(CRUDPlus[HasnWorkflow]):
    async def get(self, db: AsyncSession, pk: int) -> HasnWorkflow | None:
        """按主键获取工作流定义"""
        return await self.select_model(db, pk)

    async def get_by_uuid(self, db: AsyncSession, workflow_uuid: str) -> HasnWorkflow | None:
        """按稳定 UUID 获取工作流定义"""
        return await self.select_model_by_column(db, workflow_uuid=workflow_uuid)

    async def list_by_owner(self, db: AsyncSession, owner_id: str) -> Sequence[HasnWorkflow]:
        """获取某 owner 的全部工作流（未删除），按更新时间倒序"""
        stmt = (
            select(HasnWorkflow)
            .where(HasnWorkflow.owner_id == owner_id, HasnWorkflow.deleted_at.is_(None))
            .order_by(HasnWorkflow.updated_time.desc().nullslast(), HasnWorkflow.id.desc())
        )
        result = await db.execute(stmt)
        return result.scalars().all()


class CRUDHasnWorkflowEdge(CRUDPlus[HasnWorkflowEdge]):
    async def list_by_workflow(self, db: AsyncSession, workflow_uuid: str) -> Sequence[HasnWorkflowEdge]:
        """获取某工作流的全部依赖边"""
        stmt = select(HasnWorkflowEdge).where(HasnWorkflowEdge.workflow_uuid == workflow_uuid)
        result = await db.execute(stmt)
        return result.scalars().all()


class CRUDHasnWorkflowRun(CRUDPlus[HasnWorkflowRun]):
    async def get_by_uuid(self, db: AsyncSession, workflow_run_uuid: str) -> HasnWorkflowRun | None:
        """按稳定 UUID 获取执行实例"""
        return await self.select_model_by_column(db, workflow_run_uuid=workflow_run_uuid)

    async def list_by_workflow(
        self, db: AsyncSession, workflow_uuid: str, limit: int = 50
    ) -> Sequence[HasnWorkflowRun]:
        """获取某工作流的执行历史（倒序，限量）"""
        stmt = (
            select(HasnWorkflowRun)
            .where(HasnWorkflowRun.workflow_uuid == workflow_uuid)
            .order_by(HasnWorkflowRun.created_time.desc(), HasnWorkflowRun.id.desc())
            .limit(limit)
        )
        result = await db.execute(stmt)
        return result.scalars().all()


class CRUDHasnWorkflowNode(CRUDPlus[HasnWorkflowNode]):
    async def list_by_workflow(self, db: AsyncSession, workflow_uuid: str) -> Sequence[HasnWorkflowNode]:
        """获取某工作流的全部节点定义（按 node_key 稳定排序）"""
        stmt = (
            select(HasnWorkflowNode)
            .where(HasnWorkflowNode.workflow_uuid == workflow_uuid)
            .order_by(HasnWorkflowNode.node_key)
        )
        result = await db.execute(stmt)
        return result.scalars().all()

    async def get_by_key(
        self, db: AsyncSession, workflow_uuid: str, node_key: str
    ) -> HasnWorkflowNode | None:
        """按 (workflow_uuid, node_key) 取单个节点定义"""
        return await self.select_model_by_column(db, workflow_uuid=workflow_uuid, node_key=node_key)


class CRUDHasnWorkflowNodeRun(CRUDPlus[HasnWorkflowNodeRun]):
    async def list_by_run(self, db: AsyncSession, workflow_run_uuid: str) -> Sequence[HasnWorkflowNodeRun]:
        """获取某执行实例的全部节点执行态"""
        stmt = select(HasnWorkflowNodeRun).where(HasnWorkflowNodeRun.workflow_run_uuid == workflow_run_uuid)
        result = await db.execute(stmt)
        return result.scalars().all()

    async def get_by_run_node(
        self, db: AsyncSession, workflow_run_uuid: str, node_key: str
    ) -> HasnWorkflowNodeRun | None:
        """按 (workflow_run_uuid, node_key) 取单条节点执行态"""
        return await self.select_model_by_column(
            db, workflow_run_uuid=workflow_run_uuid, node_key=node_key
        )

    async def latest_by_workflow_node(
        self, db: AsyncSession, workflow_uuid: str, node_key: str
    ) -> HasnWorkflowNodeRun | None:
        """取某工作流某节点最近一次执行态（按 created_time 倒序取 1）"""
        stmt = (
            select(HasnWorkflowNodeRun)
            .where(
                HasnWorkflowNodeRun.workflow_uuid == workflow_uuid,
                HasnWorkflowNodeRun.node_key == node_key,
            )
            .order_by(HasnWorkflowNodeRun.created_time.desc(), HasnWorkflowNodeRun.id.desc())
            .limit(1)
        )
        result = await db.execute(stmt)
        return result.scalars().first()


hasn_workflow_dao: CRUDHasnWorkflow = CRUDHasnWorkflow(HasnWorkflow)
hasn_workflow_edge_dao: CRUDHasnWorkflowEdge = CRUDHasnWorkflowEdge(HasnWorkflowEdge)
hasn_workflow_run_dao: CRUDHasnWorkflowRun = CRUDHasnWorkflowRun(HasnWorkflowRun)
hasn_workflow_node_dao: CRUDHasnWorkflowNode = CRUDHasnWorkflowNode(HasnWorkflowNode)
hasn_workflow_node_run_dao: CRUDHasnWorkflowNodeRun = CRUDHasnWorkflowNodeRun(HasnWorkflowNodeRun)
