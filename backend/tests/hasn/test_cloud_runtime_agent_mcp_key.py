"""CLOUDRT-C：云端 provision 给云端 profile 铸 node-agnostic Agent MCP Key。

云端 hermes 注入的 MCP 工具**只能是云端 MCP**（本地 hasn-node MCP 在云端机器不可达，注入会
让上游建连失败）。云端 MCP server 用 Agent MCP Key（hasn_amk_）作 Bearer——provision 时铸的
key 必须：
- ``node_id=None``：不绑设备，否则 streamable 的 node 校验会拒云端 runtime（云端不是用户设备节点）；
- 明文前缀 ``hasn_amk_``：streamable 按此前缀分流到 key 鉴权路；
- 库内只存哈希、归属正确 agent/owner。

零 mock：真实本地 PostgreSQL，``async_db_session`` 退出即回滚，不污染权威表。
需要：export DATABASE_PORT=15432。
"""

from __future__ import annotations

import uuid

import pytest
import sqlalchemy as sa

from backend.app.hasn.model import HasnAgentMcpKeys, HasnAgents
from backend.app.hasn.service.hasn_agent_runtime_provision_service import _ensure_cloud_agent_mcp_key
from backend.database.db import async_db_session

pytestmark = pytest.mark.asyncio(loop_scope='module')


async def test_mints_node_agnostic_cloud_agent_mcp_key() -> None:
    tag = uuid.uuid4().hex[:8]
    owner = f'h_cloudrt_{tag}'
    agent = f'a_cloudrt_{tag}'
    async with async_db_session() as db:
        db.add(
            HasnAgents(
                hasn_id=agent,
                star_id=f'100001#{tag}',
                owner_id=owner,
                display_name='云端分身',
                agent_name=f'cloud_{tag}',
                type='cloud',
                role='specialist',
                api_key_hash='hash',
                status='active',
                created_via='client',
            )
        )
        await db.flush()

        # owner_user_id 仅作审计快照透传给 issue（owner_user_id 列 FK→sys_user，可空）；
        # 本测试关注 key 形态/node 绑定，故传 None 免去种一个完整 sys_user。
        key = await _ensure_cloud_agent_mcp_key(
            db, agent_hasn_id=agent, owner_hasn_id=owner, owner_user_id=None
        )

        # 明文前缀 hasn_amk_（streamable 按前缀分流到 key 鉴权路）。
        assert key.startswith('hasn_amk_')

        # 库内只存哈希、落一行；关键：node_id 为空（不绑设备），否则云端 runtime 被 node 校验拒。
        rows = (
            await db.execute(sa.select(HasnAgentMcpKeys).where(HasnAgentMcpKeys.agent_hasn_id == agent))
        ).scalars().all()
        assert len(rows) == 1
        record = rows[0]
        assert record.node_id is None
        assert record.owner_hasn_id == owner
        assert record.owner_user_id is None
        assert record.status == 'active'
        # 明文不入库（只存哈希），key_prefix 是可展示前缀。
        assert key.startswith(record.key_prefix)
