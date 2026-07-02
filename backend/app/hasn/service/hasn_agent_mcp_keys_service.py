"""Agent MCP 接入凭证 Service

见设计文档 docs/hasn-node设计文档/MCP统一工具体系/12-Agent接入凭证设计.md。
凭证形态：不透明随机串 `hasn_amk_<rand>`；落库只存 SHA-256 哈希，明文仅签发时返回一次。
生命周期靠吊销 + 轮换管理（默认不过期）；可选 node 绑定。
"""

import secrets
from typing import Sequence

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.hasn.crud.crud_hasn_agent_mcp_keys import hasn_agent_mcp_keys_dao
from backend.app.hasn.model import HasnAgentMcpKeys, HasnAgents
from backend.app.hasn.schema.hasn_agent_mcp_keys import (
    AgentMcpKeyInfo,
    IssueAgentMcpKeyParam,
    IssuedAgentMcpKey,
)
from backend.common.security.encryption import key_encryption
from backend.common.exception import errors
from backend.common.log import log
from backend.utils.timezone import timezone

# 凭证前缀：云端 MCP 鉴权按此前缀分流（见 streamable.py / 设计 §6）
KEY_PREFIX = 'hasn_amk'
# 展示前缀长度：足以辨识、不可反推完整 key
_DISPLAY_PREFIX_LEN = 16


def _generate_key() -> tuple[str, str]:
    """生成一把不透明 key，返回 (完整明文 key, 展示前缀)"""
    random_part = secrets.token_urlsafe(32)
    full_key = f'{KEY_PREFIX}_{random_part}'
    return full_key, full_key[:_DISPLAY_PREFIX_LEN]


class HasnAgentMcpKeysService:
    """Agent MCP 接入凭证服务"""

    @staticmethod
    async def _require_owned_agent(db: AsyncSession, agent_hasn_id: str, owner_hasn_id: str) -> HasnAgents:
        """确认 Agent 存在且归属当前 owner（HASN 核心原则：所有 Agent 必须有主人）"""
        agent = (
            await db.execute(sa.select(HasnAgents).where(HasnAgents.hasn_id == agent_hasn_id))
        ).scalar_one_or_none()
        if agent is None:
            raise errors.NotFoundError(msg='Agent 不存在')
        if agent.owner_id != owner_hasn_id:
            raise errors.ForbiddenError(msg='无权为该 Agent 签发凭证')
        return agent

    @staticmethod
    async def issue(
        db: AsyncSession,
        *,
        obj: IssueAgentMcpKeyParam,
        owner_hasn_id: str,
        owner_user_id: int | None,
    ) -> IssuedAgentMcpKey:
        """签发一把凭证：明文仅在返回值出现一次，库内只存哈希"""
        await HasnAgentMcpKeysService._require_owned_agent(db, obj.agent_hasn_id, owner_hasn_id)

        full_key, display_prefix = _generate_key()
        key_hash = key_encryption.hash_key(full_key)

        record = await hasn_agent_mcp_keys_dao.insert_key(
            db,
            agent_hasn_id=obj.agent_hasn_id,
            owner_hasn_id=owner_hasn_id,
            owner_user_id=owner_user_id,
            key_prefix=display_prefix,
            key_hash=key_hash,
            node_id=obj.node_id,
            expire_time=obj.expire_time,
        )

        return IssuedAgentMcpKey(
            id=record.id,
            agent_hasn_id=record.agent_hasn_id,
            owner_hasn_id=record.owner_hasn_id,
            key_prefix=record.key_prefix,
            key=full_key,  # 仅签发时返回一次
            # scopes 已退役（实施102 S0）：MCP key 不承载 scope，恒空占位。
            node_id=record.node_id,
            status=record.status,
            expire_time=record.expire_time,
            created_time=record.created_time,
        )

    @staticmethod
    async def list_for_owner(db: AsyncSession, owner_hasn_id: str) -> list[AgentMcpKeyInfo]:
        """列出 owner 名下全部凭证（不含明文/哈希）"""
        rows = await hasn_agent_mcp_keys_dao.list_by_owner(db, owner_hasn_id)
        return [AgentMcpKeyInfo.model_validate(r) for r in rows]

    @staticmethod
    async def revoke(db: AsyncSession, *, pk: int, owner_hasn_id: str) -> None:
        """吊销一把凭证（仅限本人名下）；吊销即时失效"""
        record = await hasn_agent_mcp_keys_dao.get(db, pk)
        if record is None:
            raise errors.NotFoundError(msg='凭证不存在')
        if record.owner_hasn_id != owner_hasn_id:
            raise errors.ForbiddenError(msg='无权吊销该凭证')
        if record.status != 'active':
            return
        await hasn_agent_mcp_keys_dao.revoke(db, pk)

    @staticmethod
    async def verify(
        db: AsyncSession,
        *,
        presented_key: str,
        node_id: str | None = None,
    ) -> HasnAgentMcpKeys:
        """
        校验出示的 key：哈希查表 → active → 未过期 → node 绑定匹配。
        命中刷新 last_used_time 并返回凭证行（由调用方构造 AgentContext）。
        校验失败抛 AuthorizationError（零 fake：不静默放过）。
        """
        if not presented_key:
            raise errors.AuthorizationError(msg='Invalid Agent MCP Key')

        key_hash = key_encryption.hash_key(presented_key)
        record = await hasn_agent_mcp_keys_dao.get_active_by_hash(db, key_hash)
        if record is None:
            log.warning(f'[AgentMcpKey] 校验失败：未命中 active 行 hash={key_hash[:12]}…')
            raise errors.AuthorizationError(msg='Invalid Agent MCP Key')

        # 可选过期（默认不过期；设置了才校验）
        if record.expire_time and record.expire_time < timezone.now():
            raise errors.AuthorizationError(msg='Agent MCP Key has expired')

        # node 绑定（默认开：签发即绑当前 node；空=不限设备）
        if record.node_id and record.node_id != node_id:
            log.warning(f'[AgentMcpKey] node 绑定不匹配 expect={record.node_id} got={node_id}')
            raise errors.AuthorizationError(msg='Agent MCP Key not valid for this node')

        await hasn_agent_mcp_keys_dao.touch_last_used(db, record.id)
        return record


hasn_agent_mcp_keys_service: HasnAgentMcpKeysService = HasnAgentMcpKeysService()
