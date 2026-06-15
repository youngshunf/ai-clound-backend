"""公共技能解析（doc12 §3.2 / §3.4 + doc14 §B3）。

公共技能 = `marketplace_skill.is_common=true` 且 `status='published'` 的技能。
集合修订号 `common_skills_revision` = 对 sorted([(skill_id, content_fingerprint)]) 的稳定哈希：
任一成员增删、或任一成员**内容指纹**变化 → 修订号变 → 下游（Agent profile / 同步快照）
据此让 Runtime re-provision 拉取最新公共技能。

⚠️ doc14 §B3 关键修复：指纹取 **content_hash**（源内容指纹，改正文即变）而非 version——
官方技能 frontmatter 无 version、同步器恒赋 `1.0.0`，用 version 当指纹会让"改正文不触发更新"
（doc12 饿死的更新信号）。取 ``COALESCE(content_hash, file_hash, version)``：官方/github 有
content_hash 用之；clawhub 无 content_hash 但有真实上游 version → 回落 version 仍能驱动。

同时把**公共技能包（bundle，`marketplace_template.is_common=true` 的 skill_pack）的内容指纹**
一并折进修订号——bundle 的 content_hash 由 hermes_yaml 规范化算出（改 bundle.yaml 即变），
故公共包定义变化也会触发全量 Agent re-provision（provision 本就连 skill_bundles 一起重拉）。

零 fake：无公共技能/包时修订号恒为稳定空值 ``'0'``（不臆造、不随机）。
本模块只读 DB，不依赖 hub 文件——hub `common-skills.yaml`/`common-bundles.yaml` 经 webhook 同步落 `is_common`。
"""

from __future__ import annotations

import hashlib

import sqlalchemy as sa

from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.marketplace.model.marketplace_skill import MarketplaceSkill
from backend.app.marketplace.model.marketplace_skill_version import MarketplaceSkillVersion

# 无公共技能时的稳定修订号（零 fake：固定值，非随机/时间）。
EMPTY_COMMON_SKILLS_REVISION = '0'


async def _common_bundle_fingerprints(db: AsyncSession) -> list[str]:
    """公共技能包（is_common skill_pack）的 ``bundle:{template_id}@{指纹}`` 行（排序后）。

    指纹 = 最新版本 ``COALESCE(content_hash, file_hash, version)``；content_hash 由 hermes_yaml
    规范化算出，改 bundle.yaml/成员/instruction 即变。表已搬入 hasn_marketplace（裸 SQL 须全限定）。
    """
    rows = (
        await db.execute(
            sa.text(
                """
                SELECT t.template_id AS template_id,
                       COALESCE(v.content_hash, v.file_hash, v.version, '') AS fp
                FROM hasn_marketplace.marketplace_template t
                LEFT JOIN hasn_marketplace.marketplace_template_version v
                  ON v.template_id = t.template_id AND v.is_latest = true
                WHERE t.template_type = 'skill_pack'
                  AND t.is_common = true
                  AND t.status = 'published'
                """
            )
        )
    ).mappings().all()
    by_id: dict[str, str] = {}
    for row in rows:
        tid = row.get('template_id')
        if not tid:
            continue
        key = str(tid)
        if key not in by_id:
            by_id[key] = str(row.get('fp') or '')
    return [f'bundle:{tid}@{by_id[tid]}' for tid in sorted(by_id)]


async def get_common_skill_snapshot(db: AsyncSession) -> tuple[list[str], str]:
    """返回 (公共技能 skill_id 排序清单, common_skills_revision)。

    - skill_id 升序排列，保证清单稳定可比较（清单本身只含技能，供并入 profile.skills）。
    - revision = sha256(sorted 技能 "id@指纹" 行 + sorted 公共包 "bundle:id@指纹" 行)[:16]；
      指纹取最新版本 ``COALESCE(content_hash, file_hash, version)``（doc14 §B3，缺全部记 ''）。
    """
    latest_version = (
        sa.select(
            MarketplaceSkillVersion.skill_id.label('skill_id'),
            sa.func.coalesce(
                MarketplaceSkillVersion.content_hash,
                MarketplaceSkillVersion.file_hash,
                MarketplaceSkillVersion.version,
            ).label('fingerprint'),
        )
        .where(MarketplaceSkillVersion.is_latest.is_(True))
        .subquery()
    )
    stmt = (
        sa.select(MarketplaceSkill.skill_id, latest_version.c.fingerprint)
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
    for skill_id, fingerprint in rows:
        if not skill_id:
            continue
        key = str(skill_id)
        if key not in by_id:
            by_id[key] = str(fingerprint or '')

    skill_ids = sorted(by_id)
    bundle_lines = await _common_bundle_fingerprints(db)

    # 技能与公共包都没有 → 稳定空修订号。
    if not skill_ids and not bundle_lines:
        return [], EMPTY_COMMON_SKILLS_REVISION

    skill_lines = [f'{sid}@{by_id[sid]}' for sid in skill_ids]
    signature = '\n'.join([*skill_lines, *bundle_lines])
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
