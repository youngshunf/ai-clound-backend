"""获客企业开通自播种（实施 92 GE3 / 设计 v3 §6.7）。

企业首次开通获客（enterprise holder entitlement 写入，doc 16）→ `ensure_growth_enterprise_seeded` 幂等地
把 3 套内置 playbook 复制为「企业级 playbook」（owner_scope='enterprise'，归属本企业、可被企业经理改），
让企业团队开箱即有一套可用打法。个人开通走原 personal 路径（内置 playbook 对所有人可见，无需复制）。

幂等：按 (enterprise_id, name) 去重——只插入本企业尚未有的内置同名 playbook（GE1 已建
partial unique index `uq_growth_playbook_enterprise_name`，并发下也不会重复）。重复开通不重复播种。
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import sqlalchemy as sa

from backend.app.hasn_growth.model.playbook import Playbook

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession


async def ensure_growth_enterprise_seeded(db: AsyncSession, *, enterprise_id: int) -> int:
    """企业开通获客时自播种企业 playbook（幂等）。返回本次新增的企业 playbook 条数（已存在则 0）。

    以内置 playbook（is_builtin AND user_id IS NULL）为模板，复制成本企业可见可改的 enterprise 副本：
    owner_scope='enterprise'、enterprise_id=本企业、is_builtin=False、user_id=None。
    """
    if not enterprise_id:
        return 0

    # 模板：现有内置 playbook（M5 seed 落库的 3 套）。
    templates = (
        await db.execute(
            sa.select(Playbook).where(Playbook.is_builtin.is_(True), Playbook.user_id.is_(None))
        )
    ).scalars().all()
    if not templates:
        return 0

    # 本企业已有的企业 playbook 名（幂等去重键）。
    existing_names = set(
        (
            await db.execute(
                sa.select(Playbook.name).where(
                    Playbook.owner_scope == 'enterprise',
                    Playbook.enterprise_id == enterprise_id,
                )
            )
        ).scalars().all()
    )

    seeded = 0
    for tpl in templates:
        if tpl.name in existing_names:
            continue
        db.add(
            Playbook(
                user_id=None,
                name=tpl.name,
                enabled=tpl.enabled,
                goal=tpl.goal,
                target_profile=dict(tpl.target_profile or {}),
                cadence=dict(tpl.cadence or {}) if isinstance(tpl.cadence, dict) else (tpl.cadence or {}),
                tone_guide=tpl.tone_guide,
                exit_rule=dict(tpl.exit_rule or {}),
                is_builtin=False,  # 企业副本可被经理改，非全局内置
                owner_scope='enterprise',
                enterprise_id=enterprise_id,
            )
        )
        seeded += 1

    if seeded:
        await db.flush()
    return seeded
