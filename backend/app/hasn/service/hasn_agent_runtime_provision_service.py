"""云端形态分身 provision-on-demand（双形态 Runtime，补「provision 分叉」缺口）。

设计 08/02：云端分身（runtime_location=cloud）的 runtime profile 必须由**云端**用 platform
LLM provision 到云端 hermes-runtime（与 dispatch 同侧 broker），而非 daemon 用 owner BYO 凭据
provision 到本地 sidecar。此前双形态实现了「形态落库 + dispatch 分叉 + liveness 分叉」，唯独
漏了 provision 分叉——云端分身从创建到派发从未被 provision 到云端 hermes，dispatch 必报
`profile_not_found`。本模块补上这一环，给两处共用：
- 创建时（`hasn_agents_service.create_cloud_first`，best-effort，失败不阻断创建）；
- dispatch 兜底（`hasn_agent_runtime.create_runtime_run` resolve 阶段，缺失即补，覆盖存量+自愈）。

零 fake：任一步骤失败一律抛 `HermesRuntimeError`，绝不伪造成功；调用方据语义决定阻断/兜底。
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import sqlalchemy as sa

from backend.app.hasn.model import HasnAgents, HasnHumans
from backend.app.hermes.service.hermes_runtime_client import HermesRuntimeClient, HermesRuntimeError
from backend.common.exception import errors
from backend.common.log import log
from backend.core.conf import settings

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

    from backend.app.hasn.schema.hasn_agent_mcp_keys import IssuedAgentMcpKey


def _platform_llm_model() -> str:
    model = getattr(settings, 'HUANXING_HERMES_PLATFORM_LLM_MODEL', None) or getattr(
        settings, 'HERMES_PLATFORM_LLM_MODEL', 'openai/gpt-5.5'
    )
    if not isinstance(model, str) or not model:
        raise errors.ServerError(msg='云端 Runtime 平台模型配置无效')
    return model


async def _owner_llm_credentials(db: AsyncSession, user_id: int) -> tuple[str, str]:
    """取 owner 的 new-api LLM 凭据，与本地登录 onboarding 下发给 daemon 的**完全同源**。

    返回 ``(api_key, base_url)``：``api_key = sk-{owner newapi_token_key}``、
    ``base_url = settings.LLM_API_BASE_URL``，对齐 ``SqlAlchemyLlmCredentialIssuer.issue``。
    福仔 2026-06-27：「配置逻辑要和本地一样，取登录返回的 LLM_base_url 和 LLM_api_key」。
    """
    from backend.app.admin.model import User
    from backend.app.newapi.service import llm_newapi_user_mapping_service

    user_row = (await db.execute(sa.select(User).where(User.id == user_id).limit(1))).scalar_one_or_none()
    mapping = await llm_newapi_user_mapping_service.ensure_newapi_user(
        db,
        user_id,
        username=(user_row.phone or user_row.username) if user_row else '',
        nickname=(user_row.nickname or '') if user_row else '',
    )
    base_url = settings.LLM_API_BASE_URL
    if not base_url:
        raise errors.ServerError(msg='云端 Runtime LLM API 地址未配置')
    return f'sk-{mapping.newapi_token_key}', base_url


async def _effective_main_model(db: AsyncSession, runtime_config: dict | None) -> str:
    """按 per-agent → PDC 的稳定优先级解析主模型；全空/读取失败才回落静态默认。

    云端 Hermes 不经过 daemon，仍必须遵守同一份 ``runtime_config_json`` 权威配置，不能
    无条件用平台默认覆盖 Agent 显式主模型。``build_effective_runtime_config`` 已实现
    per-agent 优先、空槽回落 PDC 的稳定契约，这里只取合并后的 main。
    """
    from backend.app.hasn.service.platform_default_config_service import platform_default_config_service

    try:
        effective = await platform_default_config_service.build_effective_runtime_config(db, runtime_config)
        models = effective.get('models') if isinstance(effective, dict) else None
        main = models.get('main') if isinstance(models, dict) else None
        if isinstance(main, str) and main:
            return main
    except Exception as exc:
        log.warning(f'Agent 主模型解析失败，回落静态默认: {exc}')
    return _platform_llm_model()


async def _resolve_owner_user_id(db: AsyncSession, owner_hasn_id: str) -> int:
    human = (
        await db.execute(sa.select(HasnHumans).where(HasnHumans.hasn_id == owner_hasn_id).limit(1))
    ).scalar_one_or_none()
    if human is None or getattr(human, 'user_id', None) is None:
        raise errors.RequestError(msg='owner user mapping missing for cloud runtime provision')
    return int(human.user_id)


async def _owner_phone(db: AsyncSession, user_id: int) -> str | None:
    """取 owner 手机号——云端 runtime owner-scoped 目录的 owner_key 用 phone（hermes 侧兜底
    owner_user_id）。落地路径 {runtime_root}/{phone}/{profile_id}/workspace，每个用户的
    agent-profile 聚合在同一手机号目录下（福仔 2026-06-27）。"""
    from backend.app.admin.model import User

    phone = (await db.execute(sa.select(User.phone).where(User.id == user_id).limit(1))).scalar_one_or_none()
    return str(phone) if phone else None


async def cloud_profile_id_for(db: AsyncSession, *, owner_hasn_id: str, agent_name: str) -> str:
    """云端分身 runtime profile_id = ``{owner.star_id}-{agent_name}``（与 daemon binding metadata
    profile_ref / dispatch 同口径）。创建时云端自算（dispatch 时由 daemon 携带，无需算）。"""
    human = (
        await db.execute(sa.select(HasnHumans).where(HasnHumans.hasn_id == owner_hasn_id).limit(1))
    ).scalar_one_or_none()
    star_id = getattr(human, 'star_id', None) if human else None
    if not star_id:
        raise errors.RequestError(msg='owner star_id missing for cloud runtime profile id')
    return f'{star_id}-{agent_name}'


async def _issue_cloud_agent_mcp_key(
    db: AsyncSession,
    *,
    agent_hasn_id: str,
    owner_hasn_id: str,
    owner_user_id: int,
) -> IssuedAgentMcpKey:
    """给云端 profile 铸一把 node-agnostic Agent MCP Key，并返回一次性签发结果。

    云端 hermes 用它作 cloud MCP server 的 Bearer。``node_id=None`` 让 streamable 跳过 node
    绑定校验（云端 runtime 不是用户设备节点）。明文不回库（库内只存哈希），由 hermes 落进
    per-profile secrets（chmod 600）。本地 import 规避潜在循环依赖，与本文件既有风格一致。
    """
    from backend.app.hasn.schema.hasn_agent_mcp_keys import IssueAgentMcpKeyParam
    from backend.app.hasn.service.hasn_agent_mcp_keys_service import hasn_agent_mcp_keys_service

    return await hasn_agent_mcp_keys_service.issue(
        db,
        obj=IssueAgentMcpKeyParam(agent_hasn_id=agent_hasn_id, scopes=['tool.call'], node_id=None),
        owner_hasn_id=owner_hasn_id,
        owner_user_id=owner_user_id,
    )


async def _ensure_cloud_agent_mcp_key(
    db: AsyncSession,
    *,
    agent_hasn_id: str,
    owner_hasn_id: str,
    owner_user_id: int,
) -> str:
    """兼容既有调用：在调用方事务中签发并只返回明文。"""
    issued = await _issue_cloud_agent_mcp_key(
        db,
        agent_hasn_id=agent_hasn_id,
        owner_hasn_id=owner_hasn_id,
        owner_user_id=owner_user_id,
    )
    return issued.key


async def _issue_cloud_agent_mcp_key_committed(
    *,
    agent_hasn_id: str,
    owner_hasn_id: str,
    owner_user_id: int,
) -> IssuedAgentMcpKey:
    """独立提交云端 profile key，确保 Runtime 回调权威 API 前凭据已经可见。"""
    from backend.database.db import async_db_session

    async with async_db_session.begin() as credential_db:
        return await _issue_cloud_agent_mcp_key(
            credential_db,
            agent_hasn_id=agent_hasn_id,
            owner_hasn_id=owner_hasn_id,
            owner_user_id=owner_user_id,
        )


async def ensure_cloud_profile_provisioned(
    db: AsyncSession,
    *,
    agent_hasn_id: str,
    owner_hasn_id: str,
    profile_id: str,
    runtime_client: HermesRuntimeClient | None = None,
    trace_id: str | None = None,
) -> bool:
    """幂等地把一个云端形态分身 provision 到云端 hermes（profile_id 即 hermes agent_id）。

    返回 True=本次新建了 profile，False=profile 已存在（仅刷新 LLM 凭据，见下）。runtime
    不可达/provision 失败一律抛 `HermesRuntimeError`（零 fake）。

    **凭据自愈**：无论 profile 是否已存在，都把最新的 owner LLM 凭据（与本地登录下发给
    daemon 的同源）install 进 profile——对齐本地「每次登录自愈下发最新 token」，让存量分身
    （历史上用 per-agent token provision）下次 dispatch 平滑切到 owner token，无需删档重建。
    """
    client = runtime_client or HermesRuntimeClient()

    # 幂等探测：profile 是否已在 hermes。「不存在」→ 走完整 provision（建骨架 + 人设）；
    # 「已存在且活跃」→ 跳过建骨架/人设，但仍刷新凭据（自愈）。Hermes 的 DELETE 是
    # 软删除，GET 仍会返回 lifecycle_status=deleted；该状态必须重走 ensure_agent 才能复活，
    # 否则后续 skills/ensure 的 active_only 路由会报 agent_not_found。
    # 「runtime 不可达」等真错误如实抛。
    profile_exists = False
    try:
        runtime_agent = await client.get_agent(profile_id, trace_id=trace_id)
        profile_exists = runtime_agent.get('lifecycle_status') != 'deleted'
    except HermesRuntimeError as exc:
        not_found = exc.status_code == 404 or 'not_found' in (exc.error or '')
        if not not_found:
            raise

    agent_row = (
        await db.execute(sa.select(HasnAgents).where(HasnAgents.hasn_id == agent_hasn_id).limit(1))
    ).scalar_one_or_none()
    if agent_row is None:
        raise errors.NotFoundError(msg='ERR_HASN_AGENT_NOT_FOUND')

    user_id = await _resolve_owner_user_id(db, owner_hasn_id)
    agent_name = getattr(agent_row, 'agent_name', None) or profile_id

    # LLM 配置取「登录返回的同源」：owner 的 new-api token + base_url（与本地 daemon
    # provision 本地 hermes **完全一致**），而非 per-agent token / 静态 HERMES_PLATFORM_LLM_*。
    # 此前用 ensure_agent_token 签的 per-agent token，其 quota 与 owner 主 token 不一致 →
    # 上游报 `Insufficient account balance`（本地用 owner token 余额充足能聊，云端却报余额
    # 不足，根因即此）。统一成 owner 凭据后云端与本地行为一致。
    owner_token, base_url = await _owner_llm_credentials(db, user_id)
    llm_model = await _effective_main_model(db, getattr(agent_row, 'runtime_config_json', None))

    # 1. ensure_agent：建 profile 骨架 + LLM 配置（agent_id 用 profile_id，与 daemon
    #    dispatch/adapter 同口径）。仅新建时调；已存在则下方只刷新凭据。
    if not profile_exists:
        # owner_phone 作 hermes owner-scoped 目录 key（{runtime_root}/{phone}/{profile_id}）；
        # 空则 hermes 侧自动兜底 owner_user_id。
        owner_phone = await _owner_phone(db, user_id)
        # 云端 hermes 注入的 MCP 工具**只能是云端 MCP**：本地 hasn-node MCP（127.0.0.1:56330）
        # 在云端机器不可达，注入会让上游建连失败（见 hermes-runtime profile_config 的 cloud 形态
        # 闸）。云端 server 用 Agent MCP Key（hasn_amk_）作 Bearer——provision 时给云端 profile
        # 铸一把 **node-agnostic**（node_id=None）的 key：云端 runtime 不是用户设备节点，绑 node
        # 会被 streamable 的 node 校验拒。scopes 仅审计快照（streamable 消费时按 agent_hasn_id
        # 活取三态策略判权，见 streamable.py D3），给 tool.call 与 daemon 本地铸 key 对齐即可。
        issued_agent_mcp_key = await _issue_cloud_agent_mcp_key_committed(
            agent_hasn_id=agent_hasn_id,
            owner_hasn_id=owner_hasn_id,
            owner_user_id=user_id,
        )
        agent_mcp_key = issued_agent_mcp_key.key
        cloud_base_url = str(settings.HUANXING_CLOUD_INTERNAL_BASE_URL or '').rstrip('/')
        if not cloud_base_url:
            raise errors.ServerError(msg='云端 Runtime 权威目录内部地址未配置')
        await client.ensure_agent(
            {
                'agent_id': profile_id,
                'owner_user_id': str(user_id),
                'owner_phone': owner_phone,
                'agent_name': agent_name,
                'avatar': getattr(agent_row, 'avatar', None),
                'timezone': 'Asia/Shanghai',
                'template': 'assistant',
                # 云端 MCP 工具凭据 → hermes 物化进 config.yaml 的 mcp_servers.cloud header。
                # 仅传云端 key，绝不传 local_mcp_key（云端注入本地 server 会断连）。
                'agent_mcp_key': agent_mcp_key,
                # required ensure 拉 Agent Profile 与个人技能包时也使用同一把 node-agnostic
                # Agent MCP Key。它是长期、可吊销凭证，不依赖 Owner cookie，也不会像短期
                # Agent JWT 到期后让无人值守的云端定时任务失去权威目录访问能力。
                'agent_jwt': agent_mcp_key,
                'cloud_base_url': cloud_base_url,
                'marketplace_base_url': cloud_base_url,
                'llm': {
                    'mode': 'platform',
                    'provider': 'openai_compatible',
                    'base_url': base_url,
                    'api_key': owner_token,
                    'model': llm_model,
                    'plan_id': getattr(settings, 'HUANXING_HERMES_PLATFORM_LLM_PLAN_ID', 'pro_monthly'),
                },
            },
            trace_id=trace_id,
        )

    # 2. 装/刷新 LLM 凭据：新建或存量自愈都把最新 owner token + base_url install 进 profile
    #    （与上面 ensure_agent 同源、与本地登录下发给 daemon 的凭据同源），不再签 per-agent token。
    await client.install_credential(
        profile_id,
        {'token_key': owner_token, 'base_url': base_url, 'default_model': llm_model},
        trace_id=trace_id,
    )

    # 3. 人设：soul_md/user_md 是 register_hasn_agent 建档即渲染的权威内容，直接 put。仅新建时
    #    写（存量人设已在 profile，避免每次 dispatch 重复 put）。空则不写。
    if not profile_exists:
        soul_md = getattr(agent_row, 'soul_md', None)
        if soul_md:
            await client.put_soul(profile_id, soul_md, trace_id=trace_id)
        user_md = getattr(agent_row, 'user_md', None)
        if user_md:
            await client.put_user_profile(profile_id, user_md, trace_id=trace_id)

    log.info(
        f'cloud runtime {"provisioned" if not profile_exists else "credential-refreshed"}: '
        f'profile={profile_id} owner_user={user_id}'
    )
    return not profile_exists
