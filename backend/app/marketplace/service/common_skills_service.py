"""公共技能解析（doc12 §3.2 / §3.4）。

公共技能 = `marketplace_skill.is_common=true` 且 `status='published'` 的技能。
集合修订号 `common_skills_revision` = 对 sorted([(skill_id, latest_version)]) 的稳定哈希：
任一成员增删、或任一成员内容版本变化 → 修订号变 → 下游（Agent profile / 同步快照）
据此让 Runtime re-provision 拉取最新公共技能。

零 fake：无公共技能时修订号恒为稳定空值 ``'0'``（不臆造、不随机）。
本模块只读 DB，不依赖 hub 文件——hub `common-skills.yaml` 经 webhook 同步落 `is_common`。
"""

from __future__ import annotations

import hashlib

import sqlalchemy as sa

from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.marketplace.model.marketplace_skill import MarketplaceSkill
from backend.app.marketplace.model.marketplace_skill_version import MarketplaceSkillVersion

# 无公共技能时的稳定修订号（零 fake：固定值，非随机/时间）。
EMPTY_COMMON_SKILLS_REVISION = '0'


async def get_common_skill_snapshot(db: AsyncSession) -> tuple[list[str], str]:
    """返回 (公共技能 skill_id 排序清单, common_skills_revision)。

    - skill_id 升序排列，保证清单稳定可比较。
    - revision = sha256(sorted "id@version" 行)[:16]；版本取最新版本行（缺版本记 ''）。
    """
    latest_version = (
        sa.select(
            MarketplaceSkillVersion.skill_id.label('skill_id'),
            MarketplaceSkillVersion.version.label('version'),
        )
        .where(MarketplaceSkillVersion.is_latest.is_(True))
        .subquery()
    )
    stmt = (
        sa.select(MarketplaceSkill.skill_id, latest_version.c.version)
        .select_from(MarketplaceSkill)
        .join(
            latest_version,
            latest_version.c.skill_id == MarketplaceSkill.skill_id,
            isouter=True,
        )
        .where(
            MarketplaceSkill.is_common.is_(True),
            MarketplaceSkill.status == 'published',
        )
    )
    rows = (await db.execute(stmt)).all()

    # 去重（同一 skill_id 若误存多条 is_latest 行，保第一条），按 skill_id 排序。
    by_id: dict[str, str] = {}
    for skill_id, version in rows:
        if not skill_id:
            continue
        key = str(skill_id)
        if key not in by_id:
            by_id[key] = str(version or '')

    skill_ids = sorted(by_id)
    if not skill_ids:
        return [], EMPTY_COMMON_SKILLS_REVISION

    signature = '\n'.join(f'{sid}@{by_id[sid]}' for sid in skill_ids)
    revision = hashlib.sha256(signature.encode('utf-8')).hexdigest()[:16]
    return skill_ids, revision


async def get_common_skill_ids(db: AsyncSession) -> list[str]:
    """仅取公共技能 skill_id 排序清单。"""
    skill_ids, _ = await get_common_skill_snapshot(db)
    return skill_ids


def merge_skill_ids(common_ids: list[str], agent_ids: list[str]) -> list[str]:
    """公共技能在前、Agent 自装在后，保序去重。"""
    merged: list[str] = []
    for sid in [*common_ids, *agent_ids]:
        if sid and sid not in merged:
            merged.append(sid)
    return merged
