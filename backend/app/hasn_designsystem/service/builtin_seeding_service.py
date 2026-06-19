"""DS-P6 官方内置设计系统播种（INSERT-only 幂等）。

读已提交的 seed JSON（父仓 `external/open-design` 精选 15 套高分品牌设计系统的四层 token 契约
bundle：tokens.css 真源 + 派生 design-tokens.json/tailwind-v4.css + 评分报告 + 组件样例 +
中文 DESIGN.md），落成 ``owner='system'``、``is_builtin=True``、``source_kind='seed'`` 的
**全局只读**设计系统 + 首版 revision。评分均 ≥ good/80、契约合规（生成器侧已设准入闸）。

幂等：``(owner='system', slug)`` 已存在则跳过（绝不覆盖）。官方扩库后重跑只补新增 slug。
对所有 owner 只读可见（``list_visible`` 的 builtin 分支），daemon 经 WSPUSH/握手对账（DS-P5）
同步本地镜像、离线可预览。本函数**不 commit**：由调用方（onboarding 事务）统一提交。
"""

from __future__ import annotations

import json

from pathlib import Path
from typing import TYPE_CHECKING, Any

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from backend.app.hasn_designsystem.model import DesignSystem, Revision
from backend.app.hasn_designsystem.service.design_system_service import BUILTIN_OWNER, _content_hash
from backend.common.log import log
from backend.utils.timezone import timezone

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

# 已提交的 seed 数据（由 seed_data/_generate_builtin_seed.py 从 open-design 生成）。
_SEED_PATH = Path(__file__).resolve().parent.parent / 'seed_data' / 'builtin_design_systems.json'

# seed 作者归属：author_kind 字典仅 human/agent，用 human + author_id='system' 表「官方系统」出品。
_SEED_AUTHOR_KIND = 'human'
_SEED_AUTHOR_ID = 'system'


def _load_seed() -> list[dict[str, Any]]:
    if not _SEED_PATH.exists():
        log.warning('[designsystem] seed JSON 缺失: {}', _SEED_PATH)
        return []
    try:
        data = json.loads(_SEED_PATH.read_text(encoding='utf-8'))
    except Exception as exc:
        log.warning('[designsystem] seed JSON 解析失败: {!r}', exc)
        return []
    return data if isinstance(data, list) else []


async def _existing_builtin_slugs(db: AsyncSession) -> set[str]:
    rows = (
        (
            await db.execute(
                select(DesignSystem.slug).where(
                    DesignSystem.owner_hasn_id == BUILTIN_OWNER,
                    DesignSystem.is_builtin.is_(True),
                )
            )
        )
        .scalars()
        .all()
    )
    return set(rows)


async def seed_builtin_design_systems(db: AsyncSession) -> list[str]:
    """INSERT-only 播种官方内置设计系统（全局，owner='system'）。返回本轮新建的 slug 列表。

    - 幂等：已存在的 builtin slug 跳过，只补新增（绝不自动覆盖 / 改名 / 降级）。
    - 每套落 ``DesignSystem(is_builtin=True)`` + 首版 ``Revision``，回填 current_revision_id /
      content_hash（与 save 同口径 ``_content_hash``，供 designsystem_revision 对账）/ 评分。
    - 并发兜底：``UNIQUE(owner_hasn_id, slug)`` 撞车 → SAVEPOINT 回滚只丢这一行，当作已存在。
    - 不 commit：调用方事务统一提交；测试侧可在同一事务断言后回滚不留脏数据。
    """
    seed = _load_seed()
    if not seed:
        return []
    existing = await _existing_builtin_slugs(db)
    now = timezone.now()
    seeded: list[str] = []
    for entry in seed:
        slug = entry.get('slug')
        if not slug or slug in existing:
            continue  # INSERT-only：已有则跳过
        content = entry.get('content') or {}
        try:
            async with db.begin_nested():  # SAVEPOINT：并发撞 UNIQUE 只回滚这一行
                d = DesignSystem(
                    owner_hasn_id=BUILTIN_OWNER,
                    name=entry.get('name') or slug,
                    slug=slug,
                    category=entry.get('category'),
                    source_kind=entry.get('source_kind') or 'seed',
                    score=entry.get('score'),
                    grade=entry.get('grade'),
                    recommend_rebuild=bool(entry.get('recommend_rebuild', False)),
                    is_builtin=True,
                    content_hash=_content_hash(content),
                )
                db.add(d)
                await db.flush()
                d.updated_time = now
                rev = Revision(
                    design_system_id=d.id,
                    rev_no=1,
                    author_kind=_SEED_AUTHOR_KIND,
                    author_id=_SEED_AUTHOR_ID,
                    note='官方内置 seed 首版',
                    tokens_css=content.get('tokens_css'),
                    design_tokens_json=content.get('design_tokens_json'),
                    tailwind_css=content.get('tailwind_css'),
                    design_md=content.get('design_md'),
                    components_html=content.get('components_html'),
                    components_manifest_json=content.get('components_manifest_json'),
                    token_contract_report_json=content.get('token_contract_report_json'),
                )
                db.add(rev)
                await db.flush()
                d.current_revision_id = rev.id
            existing.add(slug)
            seeded.append(slug)
        except IntegrityError:
            log.info('[designsystem] builtin {} 已存在 (并发撞 UNIQUE)，skip', slug)
    if seeded:
        log.info('[designsystem] 播种 {} 套官方内置设计系统: {}', len(seeded), seeded)
    return seeded
