from collections.abc import Sequence
from datetime import datetime

from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy_crud_plus import CRUDPlus

from backend.app.hasn.model import HasnAgentMcpKeys
from backend.utils.timezone import timezone


class CRUDHasnAgentMcpKeys(CRUDPlus[HasnAgentMcpKeys]):
    """Agent MCP 接入凭证数据访问（落库只存哈希，无明文/无通用写）"""

    async def get(self, db: AsyncSession, pk: int) -> HasnAgentMcpKeys | None:
        """按主键取一行"""
        return await self.select_model(db, pk)

    async def get_active_by_hash(self, db: AsyncSession, key_hash: str) -> HasnAgentMcpKeys | None:
        """按 key 哈希取 active 行（鉴权查表入口）"""
        return await self.select_model_by_column(db, key_hash=key_hash, status='active')

    async def list_by_owner(self, db: AsyncSession, owner_hasn_id: str) -> Sequence[HasnAgentMcpKeys]:
        """列出某 owner 名下全部凭证（含已吊销，供管理/审计）"""
        stmt = await self.select_order('id', 'desc', owner_hasn_id=owner_hasn_id)
        result = await db.execute(stmt)
        return list(result.scalars().all())

    async def list_by_agent(self, db: AsyncSession, agent_hasn_id: str) -> Sequence[HasnAgentMcpKeys]:
        """列出某 Agent 名下全部凭证"""
        stmt = await self.select_order('id', 'desc', agent_hasn_id=agent_hasn_id)
        result = await db.execute(stmt)
        return list(result.scalars().all())

    async def insert_key(
        self,
        db: AsyncSession,
        *,
        agent_hasn_id: str,
        owner_hasn_id: str,
        owner_user_id: int | None,
        key_prefix: str,
        key_hash: str,
        node_id: str | None,
        expire_time: datetime | None,
    ) -> HasnAgentMcpKeys:
        """落一条新凭证（只存哈希），返回带 id/created_time 的行。

        scopes 已退役（实施102 S0）：MCP key 不再承载 scope 集，授权只看三态
        capability_modes（消费时活取），故不再入参/落列。
        """
        new_obj = HasnAgentMcpKeys(
            agent_hasn_id=agent_hasn_id,
            owner_hasn_id=owner_hasn_id,
            owner_user_id=owner_user_id,
            key_prefix=key_prefix,
            key_hash=key_hash,
            node_id=node_id,
            status='active',
            expire_time=expire_time,
        )
        db.add(new_obj)
        await db.flush()
        await db.refresh(new_obj)
        return new_obj

    async def revoke(self, db: AsyncSession, pk: int) -> int:
        """吊销：置 status=revoked（行保留供审计）"""
        return await self.update_model(db, pk, {'status': 'revoked'})

    async def touch_last_used(self, db: AsyncSession, pk: int) -> int:
        """刷新最近使用时间（鉴权命中后异步调用）"""
        return await self.update_model(db, pk, {'last_used_time': timezone.now()})


hasn_agent_mcp_keys_dao: CRUDHasnAgentMcpKeys = CRUDHasnAgentMcpKeys(HasnAgentMcpKeys)
