"""创作运营企业开通自播种（设计 §6.7 业务系统型；仿 hasn_growth enterprise_seed_service）。

企业首次开通创作（enterprise holder entitlement 写入，doc 16）→ `ensure_creator_enterprise_seeded`
幂等地把内置 playbook（账号打法模板）复制为「企业级 playbook」（enterprise_id=本企业、可被企业主编改），
让企业团队开箱即有一套可用打法。个人开通走原 personal 路径（内置 playbook 对所有人可见，无需复制）。

幂等：按 (enterprise_id, name) 去重——只插入本企业尚未有的内置同名 playbook。重复开通不重复播种。
creator playbook 表无 owner_scope 列：企业 playbook 以 enterprise_id IS NOT NULL 区分。
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import sqlalchemy as sa

from backend.app.hasn_creator.model.playbook import Playbook

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession


async def ensure_creator_enterprise_seeded(db: AsyncSession, *, enterprise_id: int) -> int:
    """企业开通创作时自播种企业 playbook（幂等）。返回本次新增条数（已存在则 0）。

    以内置 playbook（is_builtin AND user_id IS NULL AND enterprise_id IS NULL）为模板，复制成
    本企业可见可改的 enterprise 副本：enterprise_id=本企业、is_builtin=False、user_id=None。
    无内置模板（M9 hub seed 之前）→ 返回 0（不造假，开通仍成功）。
    """
    if not enterprise_id:
        return 0

    templates = (
        (
            await db.execute(
                sa.select(Playbook).where(
                    Playbook.is_builtin.is_(True),
                    Playbook.user_id.is_(None),
                    Playbook.enterprise_id.is_(None),
                )
            )
        )
        .scalars()
        .all()
    )
    if not templates:
        return 0

    existing_names = set(
        (await db.execute(sa.select(Playbook.name).where(Playbook.enterprise_id == enterprise_id))).scalars().all()
    )

    seeded = 0
    for tpl in templates:
        if tpl.name in existing_names:
            continue
        db.add(
            Playbook(
                user_id=None,
                enterprise_id=enterprise_id,
                name=tpl.name,
                goal=tpl.goal,
                content_strategy=dict(tpl.content_strategy or {}),
                cadence=dict(tpl.cadence or {}),
                tone_guide=tpl.tone_guide,
                red_lines=list(tpl.red_lines or []),
                is_builtin=False,  # 企业副本可被主编改，非全局内置
            )
        )
        seeded += 1

    if seeded:
        await db.flush()
    return seeded
