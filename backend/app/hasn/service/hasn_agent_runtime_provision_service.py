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
from backend.app.newapi.service import LlmNewapiUserMappingService
from backend.common.exception import errors
from backend.common.log import log
from backend.core.conf import settings

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession


def _platform_llm_model() -> str:
    return getattr(settings, 'HUANXING_HERMES_PLATFORM_LLM_MODEL', None) or getattr(
        settings, 'HERMES_PLATFORM_LLM_MODEL', 'openai/gpt-5.5'
    )


def _platform_llm_base_url() -> str:
    return getattr(
        settings, 'HUANXING_HERMES_PLATFORM_LLM_BASE_URL', 'https://api.huanxing.ai/api/v1/llm/proxy/v1'
    )


async def _resolve_owner_user_id(db: AsyncSession, owner_hasn_id: str) -> int:
    human = (
        await db.execute(sa.select(HasnHumans).where(HasnHumans.hasn_id == owner_hasn_id).limit(1))
    ).scalar_one_or_none()
    if human is None or getattr(human, 'user_id', None) is None:
        raise errors.RequestError(msg='owner user mapping missing for cloud runtime provision')
    return int(human.user_id)


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

    返回 True=本次执行了 provision，False=profile 已存在跳过。runtime 不可达/provision 失败
    一律抛 `HermesRuntimeError`（零 fake）。
    """
    client = runtime_client or HermesRuntimeClient()

    # 幂等：profile 已在 hermes（曾被 provision 过）→ 跳过。仅「不存在」继续 provision；
    # 「runtime 不可达」等真错误如实抛，不吞。
    try:
        await client.get_agent(profile_id, trace_id=trace_id)
        return False
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
    llm_model = _platform_llm_model()
    base_url = _platform_llm_base_url()

    # 1. ensure_agent：建 profile 骨架 + platform LLM 配置（agent_id 用 profile_id，与 daemon
    #    dispatch/adapter 同口径——hermes 侧 profile_id ≈ 传入 agent_id）。幂等。
    await client.ensure_agent(
        {
            'agent_id': profile_id,
            'owner_user_id': str(user_id),
            'agent_name': agent_name,
            'avatar': getattr(agent_row, 'avatar', None),
            'timezone': 'Asia/Shanghai',
            'template': 'assistant',
            'llm': {
                'mode': 'platform',
                'provider': 'openai_compatible',
                'base_url': base_url,
                'api_key': getattr(settings, 'HUANXING_HERMES_PLATFORM_LLM_API_KEY', ''),
                'model': llm_model,
                'plan_id': getattr(settings, 'HUANXING_HERMES_PLATFORM_LLM_PLAN_ID', 'pro_monthly'),
            },
        },
        trace_id=trace_id,
    )

    # 2. LLM 凭据：经 new-api 管理 API 给该分身签独立 token（幂等），装进 profile。
    issued = await LlmNewapiUserMappingService.ensure_agent_token(db, agent_id=profile_id, user_id=user_id)
    raw_token_key = issued.get('raw_token_key')
    if raw_token_key:
        await client.install_credential(
            profile_id,
            {'token_key': f'sk-{raw_token_key}', 'base_url': base_url, 'default_model': llm_model},
            trace_id=trace_id,
        )

    # 3. 人设：soul_md/user_md 是 register_hasn_agent 建档即渲染的权威内容，直接 put（无需
    #    apply_template 模板包）。空则不写。
    soul_md = getattr(agent_row, 'soul_md', None)
    if soul_md:
        await client.put_soul(profile_id, soul_md, trace_id=trace_id)
    user_md = getattr(agent_row, 'user_md', None)
    if user_md:
        await client.put_user_profile(profile_id, user_md, trace_id=trace_id)

    log.info(f'cloud runtime provisioned: profile={profile_id} owner_user={user_id}')
    return True
