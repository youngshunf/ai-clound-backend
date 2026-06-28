"""HASN Agent Profile - Agent 端 API（云端权威化）

认证方式: DependsAgentJwtAuth（Agent JWT）
身份: **恒取自 JWT**（agent.agent_hasn_id），绝不从请求体/路径读取身份字段。

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
from backend.app.hasn.service.owner_memory_service import owner_memory_service
from backend.app.hasn.service.platform_default_config_service import platform_default_config_service
from backend.app.marketplace.service.common_skills_service import (
    get_common_skill_snapshot,
    get_installed_skills_revision,
    get_skills_content_fingerprints,
    merge_skill_ids,
)
from backend.common.dataclasses import AgentTokenPayload
from backend.common.exception import errors
from backend.common.log import log
from backend.common.response.response_schema import ResponseSchemaModel, response_base
from backend.common.security.agent_jwt_auth import DependsAgentJwtAuth
from backend.database.db import CurrentSession, CurrentSessionTransaction

router = APIRouter()


def _normalize_skill_ids(skills: Any) -> list[str]:
    """把 hasn_agents.skills（JSONB）归一化为 skill_id 字符串清单。

    兼容四种历史形态：
      - list[str]：``['huanxing/x', ...]``
      - list[{skill_id|id}]：``[{'skill_id': 'huanxing/x'}, ...]``
      - {skill_id: version}：``{'huanxing/x': '1.0'}``（key 即 skill_id，value 是版本号）
      - {'enabled': [<skill_id>...]}：旧 onboarding 误写的包裹形态——真实成员藏在
        ``enabled`` 的**值**里（list），不是 key。若不识别，会把 ``['enabled']`` 当成
        唯一技能下发，导致该 Agent 真实技能全不 provision（生产 2 个旧「全能助理」分身命中）。
    """
    if skills is None:
        return []
    if isinstance(skills, list):
        out: list[str] = []
        for item in skills:
            if isinstance(item, str) and item.strip():
                out.append(item.strip())
            elif isinstance(item, dict):
                sid = item.get('skill_id') or item.get('id')
                if sid:
                    out.append(str(sid))
        return out
    if isinstance(skills, dict):
        # 包裹形态：成员在 enabled 的值里（list）。{skill_id: version} 形态 value 是版本号
        # 字符串，故按「值是不是 list」区分两者。
        enabled = skills.get('enabled')
        if isinstance(enabled, list):
            return _normalize_skill_ids(enabled)
        return [str(key) for key in skills if key]
    return []


async def _resolve_skill_bundles(db: Any, bundles_ref: Any) -> list[dict]:
    """把 hasn_agents.skill_bundles（[{template_id, version}]）解析为 Runtime 物化所需形态。

    对每个已安装包按 (template_id, version) 取版本（缺 version 取 is_latest），join
    marketplace_template_version 输出 {bundle_slug, command_key, hermes_yaml}。已下架/缺版本的
    引用静默跳过（零 fake，不产生空壳包），Runtime 据现存清单物化 skill-bundles/*.yaml。
    """
    if not isinstance(bundles_ref, list) or not bundles_ref:
        return []
    out: list[dict] = []
    for ref in bundles_ref:
        if not isinstance(ref, dict):
            continue
        template_id = ref.get('template_id')
        if not template_id:
            continue
        version = ref.get('version')
        ver_filter = 'AND v.version = :version' if version else 'AND v.is_latest = true'
        bundle_row = (
            await db.execute(
                sa.text(
                    f"""
                    SELECT v.bundle_slug, v.command_key, v.hermes_yaml
                    FROM hasn_marketplace.marketplace_template_version v
                    JOIN hasn_marketplace.marketplace_template t ON t.template_id = v.template_id
                    WHERE v.template_id = :template_id
                      AND t.template_type = 'skill_pack'
                      {ver_filter}
                    LIMIT 1
                    """
                ),
                {'template_id': template_id, 'version': version},
            )
        ).mappings().one_or_none()
        if bundle_row is None or not bundle_row.get('hermes_yaml'):
            continue
        out.append(
            {
                'bundle_slug': bundle_row['bundle_slug'],
                'command_key': bundle_row['command_key'],
                'hermes_yaml': bundle_row['hermes_yaml'],
            }
        )
    return out


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
    agent_ids = _normalize_skill_ids(getattr(row, 'skills', None))
    # 自装技能内容修订号（doc14 §B4）：自装技能内容升级即变，Runtime 据此重拉已装技能。
    installed_rev = await get_installed_skills_revision(db, agent_ids)
    # 已安装技能包（实施/91 B2.5）：解析为 {bundle_slug, command_key, hermes_yaml}，
    # Runtime 据此物化 skill-bundles/*.yaml（成员技能已并入 skills，此处仅给 hermes 包定义）。
    skill_bundles = await _resolve_skill_bundles(db, getattr(row, 'skill_bundles', None))

    merged_skill_ids = merge_skill_ids(common_ids, agent_ids)
    # per-skill 指纹映射（doc14 §C4）：让 hermes 只重下指纹变化的技能，省全量重拉。
    skill_fingerprints = await get_skills_content_fingerprints(db, merged_skill_ids)

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
            skill_content_hashes=skill_fingerprints,
            skill_bundles=skill_bundles,
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
            sa.select(HasnAgents.profile_revision, HasnAgents.skills)
            .where(HasnAgents.hasn_id == agent.agent_hasn_id)
            .limit(1)
        )
    ).one_or_none()
    if row is None:
        raise errors.NotFoundError(msg='ERR_HASN_AGENT_NOT_FOUND')
    rev, skills = row
    # 同步叠加公共技能修订号 + 自装技能内容修订号——否则只比 profile_revision 检测不到
    # 公共技能 / 已装技能的内容变化（doc14 §B4）。
    _, common_rev = await get_common_skill_snapshot(db)
    installed_rev = await get_installed_skills_revision(db, _normalize_skill_ids(skills))
    return response_base.success(
        data=AgentProfileRevisionResponse(
            profile_revision=int(rev or 1),
            common_skills_revision=common_rev,
            installed_skills_revision=installed_rev,
        )
    )


@router.post(
    '/memory/contribute',
    summary='Agent 上传 owner 记忆观察（触发云端合并下发）',
)
async def contribute_owner_memory(
    agent: Annotated[AgentTokenPayload, DependsAgentJwtAuth],
    db: CurrentSessionTransaction,
    body: MemoryContributeRequest,
) -> ResponseSchemaModel[MemoryContributeResponse]:
    """Agent 把本地 USER.md 观察上传为 contribution，并尽力触发一次合并下发。

    owner/agent 身份恒取自 agent JWT（owner_hasn_id / agent_hasn_id），不读 body。
    合并失败不影响贡献入库：contribution 留待下次合并（零 fake，不产生假合并）。
    """
    accepted = await owner_memory_service.contribute(
        db,
        owner_id=agent.owner_hasn_id,
        agent_hasn_id=agent.agent_hasn_id,
        content=body.content,
    )
    merged = False
    version: int | None = None
    merge_deferred = False
    merge_error: str | None = None
    if accepted.get('accepted'):
        try:
            outcome = await owner_memory_service.merge_owner_memory(db, owner_id=agent.owner_hasn_id)
            merged = bool(outcome.get('merged'))
            version = outcome.get('version')
        except Exception as exc:
            # 合并失败如实透出（merge_deferred + 原因摘要），让 runtime/分身别误以为已合并、
            # 也别编造不存在的「后台异步合并」；贡献已入库、留待下次重试（零 fake）。
            merge_deferred = True
            merge_error = (str(exc).strip() or exc.__class__.__name__)[:200]
            log.warning(f'owner memory merge deferred for {agent.owner_hasn_id}: {exc}')
    return response_base.success(
        data=MemoryContributeResponse(
            accepted=bool(accepted.get('accepted')),
            merged=merged,
            version=version,
            merge_deferred=merge_deferred,
            merge_error=merge_error,
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
