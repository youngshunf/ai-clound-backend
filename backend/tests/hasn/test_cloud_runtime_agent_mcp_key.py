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

from collections.abc import Iterator
from typing import Any

import httpx
import pytest
import sqlalchemy as sa

from fastapi import FastAPI

from backend.app.admin.model.user import User
from backend.app.hasn.model import HasnAgentMcpKeys, HasnAgents
from backend.app.hasn.service.hasn_agent_runtime_provision_service import _ensure_cloud_agent_mcp_key
from backend.app.marketplace.api.v1.agent.marketplace_skill_pack import (
    router as agent_skill_pack_router,
)
from backend.common.dataclasses import AgentTokenPayload
from backend.common.security.agent_jwt_auth import DependsAgentJwtAuth
from backend.database.db import async_db_session, get_db

pytestmark = pytest.mark.asyncio(loop_scope='session')


async def test_mints_node_agnostic_cloud_agent_mcp_key() -> None:
    tag = uuid.uuid4().hex[:8]
    owner = f'h_cloudrt_{tag}'
    agent = f'a_cloudrt_{tag}'
    async with async_db_session() as db:
        user = User(
            username=f'cloudrt_{tag}',
            nickname='云端分身主人',
            password=None,
            salt=None,
        )
        db.add(user)
        await db.flush()
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

        owner_user_id = user.id
        key = await _ensure_cloud_agent_mcp_key(
            db, agent_hasn_id=agent, owner_hasn_id=owner, owner_user_id=owner_user_id
        )

        # 明文前缀 hasn_amk_（streamable 按前缀分流到 key 鉴权路）。
        assert key.startswith('hasn_amk_')

        # 库内只存哈希、落一行；关键：node_id 为空（不绑设备），否则云端 runtime 被 node 校验拒。
        rows = (
            (await db.execute(sa.select(HasnAgentMcpKeys).where(HasnAgentMcpKeys.agent_hasn_id == agent)))
            .scalars()
            .all()
        )
        assert len(rows) == 1
        record = rows[0]
        assert record.node_id is None
        assert record.owner_hasn_id == owner
        assert record.owner_user_id == owner_user_id
        assert record.status == 'active'
        # 明文不入库（只存哈希），key_prefix 是可展示前缀。
        assert key.startswith(record.key_prefix)


async def test_agent_mcp_key_self_identifies_on_agent_http_without_user_headers() -> None:
    tag = uuid.uuid4().hex[:8]
    owner = f'h_cloudrt_auth_{tag}'
    agent = f'a_cloudrt_auth_{tag}'
    async with async_db_session() as db:
        user = User(
            username=f'cloudrt_auth_{tag}',
            nickname='云端分身鉴权主人',
            password=None,
            salt=None,
        )
        db.add(user)
        await db.flush()
        db.add(
            HasnAgents(
                hasn_id=agent,
                star_id=f'100002#{tag}',
                owner_id=owner,
                display_name='云端鉴权分身',
                agent_name=f'cloud_auth_{tag}',
                type='cloud',
                runtime_location='cloud',
                role='specialist',
                api_key_hash='hash',
                status='active',
                created_via='client',
            )
        )
        await db.flush()
        key = await _ensure_cloud_agent_mcp_key(
            db,
            agent_hasn_id=agent,
            owner_hasn_id=owner,
            owner_user_id=user.id,
        )
        package_id = f'huanxing/private-runtime-{tag}'
        await db.execute(
            sa.text(
                """
                INSERT INTO hasn_marketplace.marketplace_template (
                    template_id, namespace, slug, template_type, name, author_id,
                    pricing_type, price, is_private, is_official, download_count,
                    source_type, status, created_time, updated_time
                ) VALUES (
                    :package_id, 'huanxing', :slug, 'skill_pack', '私有 Runtime 包',
                    :author_id, 'free', 0, true, false, 0, 'local', 'draft', now(), now()
                )
                """
            ),
            {
                'package_id': package_id,
                'slug': f'private-runtime-{tag}',
                'author_id': user.id,
            },
        )
        await db.execute(
            sa.text(
                """
                INSERT INTO hasn_marketplace.marketplace_template_version (
                    template_id, version, bundle_slug, command_key, hermes_yaml,
                    content_hash, file_hash, is_latest, created_time, updated_time
                ) VALUES (
                    :package_id, '1.2.3', :slug, :command_key, :hermes_yaml,
                    :content_hash, :content_hash, true, now(), now()
                )
                """
            ),
            {
                'package_id': package_id,
                'slug': f'private-runtime-{tag}',
                'command_key': f'/private-runtime-{tag}',
                'hermes_yaml': (
                    f'name: private-runtime-{tag}\n'
                    'skills:\n'
                    '  - huanxing/official/task-management\n'
                ),
                'content_hash': 'sha256:runtime-private-bundle',
            },
        )
        await db.flush()

        app = FastAPI()
        app.include_router(agent_skill_pack_router, prefix='/marketplace/agent/skill-packs')

        @app.get('/agent/whoami')
        async def whoami(
            identity: AgentTokenPayload = DependsAgentJwtAuth,
        ) -> dict[str, str | int]:
            return {
                'agent_hasn_id': identity.agent_hasn_id,
                'owner_hasn_id': identity.owner_hasn_id,
                'owner_user_id': identity.owner_user_id,
            }

        def _yield_db() -> Iterator[Any]:
            yield db

        app.dependency_overrides[get_db] = _yield_db
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app),
            base_url='http://agent-auth-e2e',
        ) as client:
            response = await client.get(
                '/agent/whoami',
                headers={'Authorization': f'Bearer {key}'},
            )
            assert response.status_code == 200, response.text
            assert response.json() == {
                'agent_hasn_id': agent,
                'owner_hasn_id': owner,
                'owner_user_id': user.id,
            }
            authority = await client.get(
                f'/marketplace/agent/skill-packs/{package_id}',
                params={'version': '1.2.3'},
                headers={'Authorization': f'Bearer {key}'},
            )
            assert authority.status_code == 200, authority.text
            assert authority.json()['data'] == {
                'package_id': package_id,
                'version': '1.2.3',
                'bundle_slug': f'private-runtime-{tag}',
                'command_key': f'/private-runtime-{tag}',
                'hermes_yaml': (
                    f'name: private-runtime-{tag}\n'
                    'skills:\n'
                    '  - huanxing/official/task-management\n'
                ),
                'content_hash': 'sha256:runtime-private-bundle',
                'member_skill_ids': ['huanxing/official/task-management'],
            }
