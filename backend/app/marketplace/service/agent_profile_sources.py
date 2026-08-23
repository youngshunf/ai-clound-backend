"""Agent Profile 的技能来源计算与冻结技能包解析。"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import sqlalchemy as sa

from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.marketplace.model import (
    MarketplacePersonalSkill,
    MarketplaceTemplate,
    MarketplaceTemplateVersion,
)
from backend.app.marketplace.service import skill_pack_service
from backend.common.log import log


@dataclass(slots=True)
class AgentProfileSkillSources:
    """Profile 技能的窄引用、有效集合和逐项来源。"""

    direct_skill_ids: list[str]
    personal_skill_ids: list[str]
    skill_bundles: list[dict[str, Any]]
    effective_skill_ids: list[str]
    origins: dict[str, list[str]]


async def get_personal_skill_immutable_snapshots(
    db: AsyncSession,
    *,
    personal_skill_ids: list[str],
    owner_user_id: int,
    owner_hasn_id: str,
) -> dict[str, dict[str, str]]:
    """返回当前主人个人技能的可验证版本快照，不泄露其他主人的同名资源。"""
    ids = sorted({skill_id for skill_id in personal_skill_ids if skill_id})
    if not ids:
        return {}
    rows = (
        (
            await db.execute(
                sa.select(MarketplacePersonalSkill).where(
                    MarketplacePersonalSkill.personal_skill_id.in_(ids),
                    sa.or_(
                        MarketplacePersonalSkill.user_id == owner_user_id,
                        MarketplacePersonalSkill.hasn_id == owner_hasn_id,
                    ),
                )
            )
        )
        .scalars()
        .all()
    )
    snapshots: dict[str, dict[str, str]] = {}
    for row in rows:
        content_hash = str(row.content_hash or row.file_hash or '').strip()
        version = int(row.version or 0)
        if row.personal_skill_id and content_hash and version > 0:
            snapshots[row.personal_skill_id] = {
                'version': f'{version}.0.0',
                'content_hash': content_hash,
            }
    return snapshots


def normalize_skill_ids(value: Any) -> list[str]:
    """把历史 JSONB 形态归一为保序去重的技能引用。"""
    candidates: list[str] = []
    if isinstance(value, list):
        for item in value:
            if isinstance(item, str):
                candidates.append(item)
            elif isinstance(item, dict):
                raw = item.get('skill_id') or item.get('id')
                if raw:
                    candidates.append(str(raw))
    elif isinstance(value, dict):
        enabled = value.get('enabled')
        if isinstance(enabled, list):
            return normalize_skill_ids(enabled)
        candidates.extend(str(key) for key in value)

    out: list[str] = []
    for candidate in candidates:
        skill_id = candidate.strip()
        if skill_id and skill_id not in out:
            out.append(skill_id)
    return out


async def classify_stored_skill_refs(
    db: AsyncSession,
    *,
    stored_refs: Any,
    owner_user_id: int,
    owner_hasn_id: str,
) -> tuple[list[str], list[str]]:
    """先反查 owner 私有库归出 personal，其余引用保守归 direct。"""
    normalized = normalize_skill_ids(stored_refs)
    if not normalized:
        return [], []

    rows = list(
        (
            await db.execute(
                sa.select(MarketplacePersonalSkill).where(
                    sa.and_(
                        sa.or_(
                            MarketplacePersonalSkill.user_id == owner_user_id,
                            MarketplacePersonalSkill.hasn_id == owner_hasn_id,
                        ),
                        sa.or_(
                            MarketplacePersonalSkill.personal_skill_id.in_(normalized),
                            MarketplacePersonalSkill.slug.in_(normalized),
                        ),
                    )
                )
            )
        )
        .scalars()
        .all()
    )
    by_id = {row.personal_skill_id: row.personal_skill_id for row in rows}
    by_slug = {row.slug: row.personal_skill_id for row in rows}

    direct: list[str] = []
    personal: list[str] = []
    for stored_ref in normalized:
        personal_id = by_id.get(stored_ref) or by_slug.get(stored_ref)
        target = personal if personal_id else direct
        canonical = personal_id or stored_ref
        if canonical not in target:
            target.append(canonical)
    return direct, personal


async def resolve_frozen_skill_bundles(db: AsyncSession, stored_refs: Any) -> list[dict[str, Any]]:
    """只按冻结 `(package_id, version)` 解析技能包，绝不跟随 latest。"""
    if not isinstance(stored_refs, list):
        return []

    resolved: list[dict[str, Any]] = []
    for ref in stored_refs:
        if not isinstance(ref, dict):
            continue
        package_id = str(ref.get('package_id') or ref.get('template_id') or '').strip()
        version = str(ref.get('version') or '').strip()
        frozen_hash = str(ref.get('content_hash') or '').strip()
        if not package_id or not version or not frozen_hash or ref.get('needs_refreeze') is True:
            log.warning(f'技能包引用尚未冻结，暂不下发 Runtime: {package_id or "unknown"}@{version or "unknown"}')
            continue

        row = (
            await db.execute(
                sa.select(MarketplaceTemplateVersion)
                .join(
                    MarketplaceTemplate,
                    MarketplaceTemplate.template_id == MarketplaceTemplateVersion.template_id,
                )
                .where(
                    MarketplaceTemplateVersion.template_id == package_id,
                    MarketplaceTemplateVersion.version == version,
                    MarketplaceTemplate.template_type == 'skill_pack',
                )
                .limit(1)
            )
        ).scalar_one_or_none()
        if row is None or not row.hermes_yaml:
            log.warning(f'技能包冻结版本不存在，暂不下发 Runtime: {package_id}@{version}')
            continue

        hermes_yaml = str(row.hermes_yaml)
        current_hash = skill_pack_service.content_hash(hermes_yaml)
        member_ids = skill_pack_service.member_skill_ids(hermes_yaml)
        try:
            member_skills = await skill_pack_service.resolve_member_skill_snapshots(
                db,
                member_ids,
                row.skill_dependencies_versioned,
            )
        except Exception as exc:
            log.warning(f'技能包 {package_id}@{version} 成员不可冻结，本次不下发 Runtime: {exc}')
            continue

        # `content_hash` 必须描述**本次真正下发的那份 `hermes_yaml`**，不能沿用安装时的冻结值。
        #
        # 技能包重发会**原地覆盖**同一个版本行的 `hermes_yaml`（同 version 不新建行），而分身
        # 安装时冻结的 hash 不会跟着变。此前这里下发的是「冻结 hash + 当前 yaml」——两个不同时点
        # 的东西拼在一起，Runtime 侧 `provision.rs` 按 `sha256(hermes_yaml) != content_hash`
        # 直接判死，报「技能包 X@Y 的 definition 指纹不一致」，该分身所有需要这个包的派发全挂。
        # 2026-08-23 线上实际发生过：改了 18 个 bundle.yaml 重发，装了它们的分身当场全失效。
        #
        # 冻结语义不受影响：**冻结锁的是 `(package_id, version)`**，上面的查询始终按冻结版本取行、
        # 绝不跟随 latest；而 `hermes_yaml` 本来就一直取当前行内容，从没按冻结 hash 回溯过历史内容
        # （版本行被原地覆盖，历史内容根本不存在）。所以下发当前 hash 是**让指纹与载荷自洽**，
        # 不是放宽版本冻结。
        #
        # 安装时的冻结值改由 `frozen_content_hash` 如实带出，`bundle_drift` 继续如实报告漂移——
        # 它们是观测信号，不再当作会打死分身的判据。
        resolved.append({
            'package_id': package_id,
            'version': version,
            'content_hash': current_hash,
            'bundle_slug': str(ref.get('bundle_slug') or row.bundle_slug or ''),
            'command_key': row.command_key,
            'hermes_yaml': hermes_yaml,
            'member_skill_ids': member_ids,
            'member_skills': member_skills,
            'bundle_drift': current_hash != frozen_hash,
            'frozen_content_hash': frozen_hash,
            'current_content_hash': current_hash,
        })
        if current_hash != frozen_hash:
            log.warning(
                f'技能包 {package_id}@{version} 定义已被原地重发覆盖'
                f'（安装时冻结 {frozen_hash} → 当前 {current_hash}）；'
                f'本次按当前定义下发以保证指纹自洽'
            )
    return resolved


def _append_origin(origins: dict[str, list[str]], skill_id: str, origin: str) -> None:
    values = origins.setdefault(skill_id, [])
    if origin not in values:
        values.append(origin)


async def build_agent_profile_skill_sources(
    db: AsyncSession,
    *,
    stored_skill_refs: Any,
    stored_bundle_refs: Any,
    common_skill_ids: list[str],
    owner_user_id: int,
    owner_hasn_id: str,
) -> AgentProfileSkillSources:
    """计算 common/direct/personal/skill_pack 的有效并集与来源。"""
    direct_ids, personal_ids = await classify_stored_skill_refs(
        db,
        stored_refs=stored_skill_refs,
        owner_user_id=owner_user_id,
        owner_hasn_id=owner_hasn_id,
    )
    bundles = await resolve_frozen_skill_bundles(db, stored_bundle_refs)
    origins: dict[str, list[str]] = {}
    effective: list[str] = []

    for origin, skill_ids in (
        ('common', common_skill_ids),
        ('direct', direct_ids),
        ('personal', personal_ids),
    ):
        for skill_id in skill_ids:
            _append_origin(origins, skill_id, origin)
            if skill_id not in effective:
                effective.append(skill_id)

    for bundle in bundles:
        bundle_origin = f'skill_pack:{bundle["package_id"]}@{bundle["version"]}'
        for skill_id in bundle['member_skill_ids']:
            _append_origin(origins, skill_id, bundle_origin)
            if skill_id not in effective:
                effective.append(skill_id)

    return AgentProfileSkillSources(
        direct_skill_ids=direct_ids,
        personal_skill_ids=personal_ids,
        skill_bundles=bundles,
        effective_skill_ids=effective,
        origins=origins,
    )
