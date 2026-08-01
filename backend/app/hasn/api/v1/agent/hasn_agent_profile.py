"""HASN Agent Profile - Agent 端 API（云端权威化）

认证方式: DependsAgentJwtAuth（Agent JWT / Agent MCP Key）
身份: **恒取自已验证凭证**（agent.agent_hasn_id），绝不从请求体/路径读取身份字段。

Runtime（huanxing-hermes-runtime）用 agent JWT 直连这里拉取自己 Agent 的 Profile，
物化为本地 SOUL.md/AGENTS.md/USER.md/MEMORY.md + 按 skills 清单下载技能包。
见 decisions/architecture/2026-05-30-agent-profile-cloud-authoritative.md §5.2。
"""

from typing import Annotated, Any

import sqlalchemy as sa

from fastapi import APIRouter

from backend.app.hasn.model import HasnAgents
from backend.app.hasn.schema.hasn_agents import (
    AgentProfileResponse,
    AgentProfileRevisionResponse,
    MemoryContributeRequest,
    MemoryContributeResponse,
    OwnerMemoryResponse,
)
from backend.app.hasn.service.owner_memory_service import MEMORY_CONTRIBUTE_PENDING_NOTE, owner_memory_service
from backend.app.hasn.service.platform_default_config_service import platform_default_config_service
from backend.app.marketplace.service.agent_profile_sources import (
    build_agent_profile_skill_sources,
    normalize_skill_ids,
)
from backend.app.marketplace.service.common_skills_service import (
    get_common_skill_snapshot,
    get_installed_skills_revision,
    get_skills_content_fingerprints,
    get_skills_immutable_snapshots,
    merge_skill_ids,
)
from backend.common.dataclasses import AgentTokenPayload
from backend.common.exception import errors
from backend.common.log import log
from backend.common.response.response_schema import ResponseSchemaModel, response_base
from backend.common.security.agent_jwt_auth import DependsAgentJwtAuth
from backend.database.db import CurrentSession, CurrentSessionTransaction

router = APIRouter()

# 保留旧测试与同仓调用点使用的私有别名；新代码统一使用公开函数名。
_normalize_skill_ids = normalize_skill_ids


@router.get(
    '/profile',
    summary='Agent 直连拉取自己的 Profile（云端权威）',
)
async def get_agent_profile(
    agent: Annotated[AgentTokenPayload, DependsAgentJwtAuth],
    db: CurrentSession,
) -> ResponseSchemaModel[AgentProfileResponse]:
    row = (
        await db.execute(sa.select(HasnAgents).where(HasnAgents.hasn_id == agent.agent_hasn_id).limit(1))
    ).scalar_one_or_none()
    if row is None:
        raise errors.NotFoundError(msg='ERR_HASN_AGENT_NOT_FOUND')

    # 读取时叠加公共技能（doc12 §3.3）：公共集合在前、Agent 自装在后，保序去重。
    # 公共技能不持久化进 hasn_agents.skills，只在出参叠加——成员/版本变化对全量 Agent
    # 自动生效，零回填。common_skills_revision 让 Runtime 据以重拉最新公共技能。
    common_ids, common_rev = await get_common_skill_snapshot(db)
    sources = await build_agent_profile_skill_sources(
        db,
        stored_skill_refs=getattr(row, 'skills', None),
        stored_bundle_refs=getattr(row, 'skill_bundles', None),
        common_skill_ids=common_ids,
        owner_user_id=agent.owner_user_id,
        owner_hasn_id=agent.owner_hasn_id,
    )
    # 自装技能内容修订号（doc14 §B4）：自装技能内容升级即变，Runtime 据此重拉已装技能。
    installed_rev = await get_installed_skills_revision(
        db,
        [skill_id for skill_id in sources.effective_skill_ids if skill_id not in common_ids],
    )
    merged_skill_ids = sources.effective_skill_ids
    # per-skill 指纹映射（doc14 §C4）：让 hermes 只重下指纹变化的技能，省全量重拉。
    skill_fingerprints = await get_skills_content_fingerprints(db, merged_skill_ids)
    skill_versions = await get_skills_immutable_snapshots(db, merged_skill_ids)

    # PDC：把平台默认 agent 运行时四槽 coalesce 进 runtime_config（runtime-facing 拉取式兜底）。
    # agent 显式非空必胜，None → 平台默认；agent 无配置且平台四槽全空 → None（保持"全默认"）。
    effective_runtime_config = await platform_default_config_service.build_effective_runtime_config(
        db, getattr(row, 'runtime_config_json', None)
    )

    return response_base.success(
        data=AgentProfileResponse(
            hasn_id=row.hasn_id,
            display_name=row.display_name,
            runtime_location=getattr(row, 'runtime_location', 'local') or 'local',
            soul_md=row.soul_md,
            agents_md=getattr(row, 'agents_md', None),
            user_md=row.user_md,
            memory_md=getattr(row, 'memory_md', None),
            skills=merged_skill_ids,
            direct_skill_ids=sources.direct_skill_ids,
            # 公共技能子集单列下发（doc11 §5.2）：hermes 据此分流「公共→共享目录 external_dirs /
            # 私有→per-profile 物化」；旧 runtime 不认识该字段则忽略，向后兼容。
            common_skill_ids=common_ids,
            personal_skill_ids=sources.personal_skill_ids,
            origins=sources.origins,
            skill_content_hashes=skill_fingerprints,
            skill_versions=skill_versions,
            skill_bundles=sources.skill_bundles,
            template_id=row.template_id,
            template_version=getattr(row, 'template_version', None),
            profile_revision=int(getattr(row, 'profile_revision', 1) or 1),
            common_skills_revision=common_rev,
            installed_skills_revision=installed_rev,
            # hermes runtime 原生配置下行（拉取式兜底，补充 daemon PUT 的即时 push）：
            # Runtime provision/reconcile 时据此写 config.yaml/.env。空=全默认。
            # 已 coalesce 平台默认四槽（PDC）：per-agent 未设的模型槽回落平台默认。
            runtime_config=effective_runtime_config,
        )
    )


@router.get(
    '/profile/revision',
    summary='Agent 轮询自己的 Profile 修订号（轻量，用于记忆下发检测）',
)
async def get_agent_profile_revision(
    agent: Annotated[AgentTokenPayload, DependsAgentJwtAuth],
    db: CurrentSession,
) -> ResponseSchemaModel[AgentProfileRevisionResponse]:
    row = (
        await db.execute(
            sa
            .select(
                HasnAgents.profile_revision,
                HasnAgents.skills,
                HasnAgents.skill_bundles,
            )
            .where(HasnAgents.hasn_id == agent.agent_hasn_id)
            .limit(1)
        )
    ).one_or_none()
    if row is None:
        raise errors.NotFoundError(msg='ERR_HASN_AGENT_NOT_FOUND')
    rev, stored_skill_refs, stored_bundle_refs = row
    # 同步叠加公共技能修订号 + 自装技能内容修订号——否则只比 profile_revision 检测不到
    # 公共技能 / 已装技能的内容变化（doc14 §B4）。
    common_ids, common_rev = await get_common_skill_snapshot(db)
    sources = await build_agent_profile_skill_sources(
        db,
        stored_skill_refs=stored_skill_refs,
        stored_bundle_refs=stored_bundle_refs,
        common_skill_ids=common_ids,
        owner_user_id=agent.owner_user_id,
        owner_hasn_id=agent.owner_hasn_id,
    )
    installed_rev = await get_installed_skills_revision(
        db,
        [skill_id for skill_id in sources.effective_skill_ids if skill_id not in common_ids],
    )
    return response_base.success(
        data=AgentProfileRevisionResponse(
            profile_revision=int(rev or 1),
            common_skills_revision=common_rev,
            installed_skills_revision=installed_rev,
        )
    )


@router.post(
    '/memory/contribute',
    summary='Agent 上传 owner 记忆观察（入贡献流，待主脑下次整理并入）',
)
async def contribute_owner_memory(
    agent: Annotated[AgentTokenPayload, DependsAgentJwtAuth],
    db: CurrentSessionTransaction,
    body: MemoryContributeRequest,
) -> ResponseSchemaModel[MemoryContributeResponse]:
    """Agent 把本地 USER.md 观察上传为 contribution。

    owner/agent 身份恒取自 agent JWT（owner_hasn_id / agent_hasn_id），不读 body。

    **doc19 §10（2026-07-31）**：端点与语义保留，实现改为「只落贡献流，不再内联合并」——
    云端 LLM 合并已整体退役，合并由**主脑分身在它自己的设备上**做（§5.1）。响应如实反映
    「已记录，将在下次整理时并入」，**不假装已合并**（零 fake）。
    """
    accepted = await owner_memory_service.contribute(
        db,
        owner_id=agent.owner_hasn_id,
        agent_hasn_id=agent.agent_hasn_id,
        content=body.content,
    )
    memory = await owner_memory_service.get_owner_memory(db, owner_id=agent.owner_hasn_id)
    is_accepted = bool(accepted.get('accepted'))
    return response_base.success(
        data=MemoryContributeResponse(
            accepted=is_accepted,
            contribution_id=accepted.get('contribution_id'),
            pending_merge=is_accepted,
            merge_note=MEMORY_CONTRIBUTE_PENDING_NOTE if is_accepted else '',
            owner_memory_version=int(memory.get('version') or 0),
            reason=None if is_accepted else accepted.get('reason'),
        )
    )


@router.get(
    '/memory',
    summary='Agent 拉取当前 owner 记忆（下发的 USER.md）',
)
async def get_owner_memory(
    agent: Annotated[AgentTokenPayload, DependsAgentJwtAuth],
    db: CurrentSession,
) -> ResponseSchemaModel[OwnerMemoryResponse]:
    memory = await owner_memory_service.get_owner_memory(db, owner_id=agent.owner_hasn_id)
    return response_base.success(
        data=OwnerMemoryResponse(content=memory.get('content'), version=int(memory.get('version') or 0))
    )
