"""DS-P6 官方内置设计系统播种（reconcile：新增 / 内容变更替换 / 退役删除）。

读已提交的 seed JSON（唤星自研生成器 `crates/hasn-designsystem-core/examples/gen_builtin.rs` 用
确定性 token 契约引擎产出的 12 套产品视角中文设计系统：tokens.css 真源 + 派生 design-tokens.json/
tailwind-v4.css + 评分报告 + 组件样例 + 中文 DESIGN.md + 列表卡预览色板），落成 ``owner='system'``、
``is_builtin=True``、``source_kind='seed'`` 的**全局只读**设计系统 + 首版 revision。评分均
excellent/100（生成器侧设了「非 excellent 即报错」的准入闸）。

reconcile（非 INSERT-only——官方库要能换代）：以 seed 的 slug 集为权威，
- 新 slug → 新建；
- 已有 slug 且 content_hash 变化 → 落新版 revision 并切当前版（替换内容、刷新评分/预览，保留历史）；
- 已有 slug 且未变 → 跳过（幂等）；
- DB 里有、seed 里没有的 builtin slug → 软删（退役旧官方库，如历史遗留的占位条目）。
对所有 owner 只读可见（``list_visible`` 的 builtin 分支），daemon 经 WSPUSH/握手对账（DS-P5）同步
本地镜像 + reconcile_builtins 清掉本地残留。本函数**不 commit**：由调用方（onboarding 事务）统一提交。
"""

from __future__ import annotations

import json

from pathlib import Path
from typing import TYPE_CHECKING, Any

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError

from backend.app.hasn_designsystem.model import DesignSystem, Revision
from backend.app.hasn_designsystem.service.design_system_service import (
    BUILTIN_OWNER,
    _content_hash,
    _extract_preview_swatches,
)
from backend.common.log import log
from backend.utils.timezone import timezone

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

# 已提交的 seed 数据（由 seed_data/_generate_builtin_seed.py 调 Rust 生成器产出）。
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


async def _existing_builtins(db: AsyncSession) -> dict[str, DesignSystem]:
    """当前所有 builtin（owner='system'，含已软删？否——仅未删的参与 reconcile）→ 按 slug 索引。"""
    rows = (
        (
            await db.execute(
                select(DesignSystem).where(
                    DesignSystem.owner_hasn_id == BUILTIN_OWNER,
                    DesignSystem.is_builtin.is_(True),
                    DesignSystem.deleted_time.is_(None),
                )
            )
        )
        .scalars()
        .all()
    )
    return {d.slug: d for d in rows}


def _entry_preview_swatches(entry: dict[str, Any], content: dict[str, Any]) -> dict[str, str] | None:
    """取预览色板：优先生成器已产出的 preview_swatches（权威），否则从 tokens.css 兜底 denorm。"""
    swatches = entry.get('preview_swatches')
    if isinstance(swatches, dict) and swatches:
        return swatches
    return _extract_preview_swatches(content.get('tokens_css'))


def _assign_root(d: DesignSystem, entry: dict[str, Any], content: dict[str, Any], hashed: str, now: Any) -> None:
    """把 seed 项的根字段写到 design_system（新建/换代共用；不碰 owner/slug/is_builtin）。"""
    d.name = entry.get('name') or d.slug
    d.category = entry.get('category')
    d.source_kind = entry.get('source_kind') or 'seed'
    d.score = entry.get('score')
    d.grade = entry.get('grade')
    d.recommend_rebuild = bool(entry.get('recommend_rebuild'))
    d.content_hash = hashed
    d.preview_swatches = _entry_preview_swatches(entry, content)
    d.updated_time = now


def _build_revision(d: DesignSystem, content: dict[str, Any], rev_no: int, note: str) -> Revision:
    return Revision(
        design_system_id=d.id,
        rev_no=rev_no,
        author_kind=_SEED_AUTHOR_KIND,
        author_id=_SEED_AUTHOR_ID,
        note=note,
        tokens_css=content.get('tokens_css'),
        design_tokens_json=content.get('design_tokens_json'),
        tailwind_css=content.get('tailwind_css'),
        design_md=content.get('design_md'),
        components_html=content.get('components_html'),
        components_manifest_json=content.get('components_manifest_json'),
        token_contract_report_json=content.get('token_contract_report_json'),
    )


async def seed_builtin_design_systems(db: AsyncSession) -> dict[str, list[str]]:
    """reconcile 官方内置设计系统（全局，owner='system'）。返回 {created, updated, retired} 的 slug 列表。

    - 新增：seed 有、DB 无 → 建 DesignSystem(is_builtin) + 首版 Revision，回填 current/hash/评分/预览。
    - 换代：DB 有、content_hash 变化 → 落新版 Revision、切当前版、刷新根字段（保留历史可回滚）。
    - 不变：content_hash 相同 → 跳过（幂等，可反复重跑）。
    - 退役：DB 有、seed 无 → 软删（deleted_time），清掉历史遗留官方库占位（daemon 端 reconcile 清镜像）。
    - 并发兜底：新建撞 ``UNIQUE(owner,slug)`` → SAVEPOINT 回滚只丢这一行，当作已存在。
    - 不 commit：调用方事务统一提交；测试侧可在同一事务断言后回滚不留脏数据。
    """
    seed = _load_seed()
    if not seed:
        return {'created': [], 'updated': [], 'retired': []}
    existing = await _existing_builtins(db)
    now = timezone.now()
    seed_slugs: set[str] = set()
    created: list[str] = []
    updated: list[str] = []

    for entry in seed:
        slug = entry.get('slug')
        if not slug:
            continue
        seed_slugs.add(slug)
        content = entry.get('content') or {}
        hashed = _content_hash(content)
        current = existing.get(slug)

        if current is None:
            # 新建：SAVEPOINT 兜并发撞 UNIQUE。
            try:
                async with db.begin_nested():
                    d = DesignSystem(
                        owner_hasn_id=BUILTIN_OWNER, name=entry.get('name') or slug, slug=slug, is_builtin=True
                    )
                    db.add(d)
                    await db.flush()
                    _assign_root(d, entry, content, hashed, now)
                    rev = _build_revision(d, content, 1, '官方内置 seed 首版')
                    db.add(rev)
                    await db.flush()
                    d.current_revision_id = rev.id
                created.append(slug)
            except IntegrityError:
                log.info('[designsystem] builtin {} 已存在 (并发撞 UNIQUE)，skip', slug)
            continue

        if current.content_hash == hashed:
            continue  # 未变 → 幂等跳过

        # 换代：落新版 + 切当前版 + 刷新根字段（保留历史）。
        max_rev = (
            await db.execute(
                select(func.coalesce(func.max(Revision.rev_no), 0)).where(Revision.design_system_id == current.id)
            )
        ).scalar_one()
        rev = _build_revision(current, content, int(max_rev) + 1, '官方内置 seed 换代')
        db.add(rev)
        await db.flush()
        _assign_root(current, entry, content, hashed, now)
        current.current_revision_id = rev.id
        updated.append(slug)

    # 退役：DB 有、seed 无 → 软删（历史遗留官方库占位条目，如旧『官方暖沙』）。
    retired: list[str] = []
    for slug, d in existing.items():
        if slug not in seed_slugs:
            d.deleted_time = now
            retired.append(slug)

    if created or updated or retired:
        log.info(
            '[designsystem] reconcile 官方内置：新建 {} / 换代 {} / 退役 {}',
            created,
            updated,
            retired,
        )
    return {'created': created, 'updated': updated, 'retired': retired}
