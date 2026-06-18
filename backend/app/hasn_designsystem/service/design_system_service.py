"""设计系统生成应用（app_id=designsystem）云端业务服务。

云端是**权威存储**：确定性 token 契约引擎（compile/derive/validate/extract）在 daemon 本地 Rust
crate 跑，云端不重算，只存分身/人算好的 tokens.css + 派生产物 + 评分报告，并维护版本与同步水位。

可见域（list_visible）：builtin ∪ owner ∪ 企业（共享 ACL 在 P9 接 resource_share 补齐）。
owner 隔离：写操作强制 owner_hasn_id == subject.owner_hasn_id；builtin（is_builtin）跨 owner 只读。

同步水位（designsystem_revision）：owner 维度 content-hash 聚合（照搬 SKAU/PDC 范式，按需计算、
无独立存储），任一可见 design_system 的 content_hash 变化即变；daemon 据此增量重拉（P5 WSPUSH）。
"""

from __future__ import annotations

import hashlib
import json

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from sqlalchemy import func, or_, select

from backend.app.hasn_designsystem.model import Collaborator, DesignSystem, Revision
from backend.common.exception import errors
from backend.utils.timezone import timezone

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

# builtin（官方内置）设计系统的归属 owner 哨兵：对所有 owner 只读可见、不属任何真实 owner。
BUILTIN_OWNER = 'system'

# 进 content_hash 与 revision 的内容字段（真源 + 派生 + 创意）。
_REVISION_CONTENT = (
    'tokens_css',
    'design_tokens_json',
    'tailwind_css',
    'design_md',
    'components_html',
    'components_manifest_json',
    'token_contract_report_json',
)


@dataclass(frozen=True)
class Subject:
    """操作主体：人或分身（分身背后总有主人）。"""

    hasn_id: str
    kind: str  # 'human' | 'agent'
    owner_hasn_id: str  # 背后主人（human 时 == hasn_id）

    @staticmethod
    def human(hasn_id: str) -> 'Subject':
        return Subject(hasn_id=hasn_id, kind='human', owner_hasn_id=hasn_id)

    @staticmethod
    def agent(agent_hasn_id: str, owner_hasn_id: str) -> 'Subject':
        return Subject(hasn_id=agent_hasn_id, kind='agent', owner_hasn_id=owner_hasn_id)


def _content_hash(payload: dict[str, Any]) -> str:
    """对一版内容算确定性 sha256（与 daemon 镜像比对触发增量重拉）。"""
    canonical = {k: payload.get(k) for k in _REVISION_CONTENT}
    blob = json.dumps(canonical, ensure_ascii=False, sort_keys=True, separators=(',', ':'))
    return hashlib.sha256(blob.encode('utf-8')).hexdigest()


def _ds_dict(d: DesignSystem) -> dict[str, Any]:
    return {
        'id': d.id,
        'owner_hasn_id': d.owner_hasn_id,
        'name': d.name,
        'slug': d.slug,
        'category': d.category,
        'source_kind': d.source_kind,
        'score': d.score,
        'grade': d.grade,
        'recommend_rebuild': d.recommend_rebuild,
        'is_builtin': d.is_builtin,
        'enterprise_id': d.enterprise_id,
        'current_revision_id': d.current_revision_id,
        'content_hash': d.content_hash,
        'created_time': d.created_time.isoformat() if d.created_time else None,
        'updated_time': d.updated_time.isoformat() if d.updated_time else None,
    }


def _revision_dict(r: Revision, *, with_content: bool = True) -> dict[str, Any]:
    out: dict[str, Any] = {
        'id': r.id,
        'design_system_id': r.design_system_id,
        'rev_no': r.rev_no,
        'author_kind': r.author_kind,
        'author_id': r.author_id,
        'bundle_asset_id': r.bundle_asset_id,
        'note': r.note,
        'created_time': r.created_time.isoformat() if r.created_time else None,
    }
    if with_content:
        out.update(
            {
                'tokens_css': r.tokens_css,
                'design_tokens_json': r.design_tokens_json,
                'tailwind_css': r.tailwind_css,
                'design_md': r.design_md,
                'components_html': r.components_html,
                'components_manifest_json': r.components_manifest_json,
                'token_contract_report_json': r.token_contract_report_json,
            }
        )
    return out


class DesignSystemService:
    """设计系统云端服务：save（落 revision + bump）、list/get（可见域）、协作分身绑定。"""

    @staticmethod
    async def _get_alive(db: AsyncSession, design_system_id: int) -> DesignSystem:
        d = await db.get(DesignSystem, design_system_id)
        if d is None or d.deleted_time is not None:
            raise errors.NotFoundError(msg='设计系统不存在')
        return d

    @staticmethod
    def _assert_readable(d: DesignSystem, viewer_owner_hasn_id: str, *, enterprise_id: int | None = None) -> None:
        if d.is_builtin:
            return
        if d.owner_hasn_id == viewer_owner_hasn_id:
            return
        if enterprise_id is not None and d.enterprise_id == enterprise_id:
            return
        raise errors.ForbiddenError(msg='无权访问该设计系统')

    async def save(
        self,
        db: AsyncSession,
        *,
        subject: Subject,
        design_system_id: int | None,
        slug: str,
        name: str,
        content: dict[str, Any],
        category: str | None = None,
        source_kind: str = 'generated',
        score: int | None = None,
        grade: str | None = None,
        recommend_rebuild: bool = False,
        bundle_asset_id: str | None = None,
        note: str | None = None,
        enterprise_id: int | None = None,
    ) -> dict[str, Any]:
        """创建或更新一套设计系统：落一版 revision + 回填 current/content_hash/评分。

        `content` 含 tokens.css + 派生 + 创意（见 _REVISION_CONTENT）。每次 save 都出一版（可回滚）。
        """
        owner = subject.owner_hasn_id
        hashed = _content_hash(content)
        now = timezone.now()

        if design_system_id is None:
            # 新建：slug 在 owner 维度唯一（撞 slug → 视为更新已存在的同 slug 行）
            existing = (
                await db.execute(
                    select(DesignSystem).where(
                        DesignSystem.owner_hasn_id == owner,
                        DesignSystem.slug == slug,
                        DesignSystem.deleted_time.is_(None),
                    )
                )
            ).scalar_one_or_none()
            if existing is not None:
                d = existing
            else:
                d = DesignSystem(
                    owner_hasn_id=owner,
                    name=name,
                    slug=slug,
                    category=category,
                    source_kind=source_kind,
                    enterprise_id=enterprise_id,
                    content_hash='',
                )
                db.add(d)
                await db.flush()
        else:
            d = await self._get_alive(db, design_system_id)
            if d.owner_hasn_id != owner or d.is_builtin:
                raise errors.ForbiddenError(msg='无权修改该设计系统')

        # 根字段更新（白名单：不允许改 owner/slug/is_builtin）
        d.name = name
        if category is not None:
            d.category = category
        d.source_kind = source_kind
        d.score = score
        d.grade = grade
        d.recommend_rebuild = recommend_rebuild
        d.content_hash = hashed
        d.updated_time = now

        # 落新版 revision（rev_no = 当前 max + 1）
        max_rev = (
            await db.execute(
                select(func.coalesce(func.max(Revision.rev_no), 0)).where(Revision.design_system_id == d.id)
            )
        ).scalar_one()
        rev = Revision(
            design_system_id=d.id,
            rev_no=int(max_rev) + 1,
            author_kind=subject.kind,
            author_id=subject.hasn_id,
            bundle_asset_id=bundle_asset_id,
            note=note,
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
        await db.commit()
        await db.refresh(d)
        out = _ds_dict(d)
        out['revision'] = _revision_dict(rev, with_content=False)
        return out

    async def list_visible(
        self,
        db: AsyncSession,
        *,
        viewer_owner_hasn_id: str,
        enterprise_id: int | None = None,
        category: str | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> dict[str, Any]:
        """可见域 = builtin ∪ owner ∪ 企业（共享 ACL 在 P9 补）。"""
        conds = [DesignSystem.is_builtin.is_(True), DesignSystem.owner_hasn_id == viewer_owner_hasn_id]
        if enterprise_id is not None:
            conds.append(DesignSystem.enterprise_id == enterprise_id)
        where = [DesignSystem.deleted_time.is_(None), or_(*conds)]
        if category:
            where.append(DesignSystem.category == category)
        total = (await db.execute(select(func.count()).select_from(DesignSystem).where(*where))).scalar_one()
        rows = (
            (
                await db.execute(
                    select(DesignSystem)
                    .where(*where)
                    .order_by(
                        DesignSystem.is_builtin.desc(), DesignSystem.updated_time.desc(), DesignSystem.id.desc()
                    )
                    .limit(limit)
                    .offset(offset)
                )
            )
            .scalars()
            .all()
        )
        return {'total': int(total), 'items': [_ds_dict(r) for r in rows]}

    async def get(
        self,
        db: AsyncSession,
        *,
        design_system_id: int,
        viewer_owner_hasn_id: str,
        enterprise_id: int | None = None,
        with_current_revision: bool = True,
    ) -> dict[str, Any]:
        d = await self._get_alive(db, design_system_id)
        self._assert_readable(d, viewer_owner_hasn_id, enterprise_id=enterprise_id)
        out = _ds_dict(d)
        if with_current_revision and d.current_revision_id is not None:
            rev = await db.get(Revision, d.current_revision_id)
            out['current_revision'] = _revision_dict(rev) if rev is not None else None
        return out

    async def delete(self, db: AsyncSession, *, design_system_id: int, owner_hasn_id: str) -> None:
        d = await self._get_alive(db, design_system_id)
        if d.owner_hasn_id != owner_hasn_id or d.is_builtin:
            raise errors.ForbiddenError(msg='无权删除该设计系统')
        d.deleted_time = timezone.now()
        await db.commit()

    async def list_revisions(
        self,
        db: AsyncSession,
        *,
        design_system_id: int,
        viewer_owner_hasn_id: str,
        enterprise_id: int | None = None,
    ) -> dict[str, Any]:
        d = await self._get_alive(db, design_system_id)
        self._assert_readable(d, viewer_owner_hasn_id, enterprise_id=enterprise_id)
        rows = (
            (
                await db.execute(
                    select(Revision)
                    .where(Revision.design_system_id == design_system_id)
                    .order_by(Revision.rev_no.desc())
                )
            )
            .scalars()
            .all()
        )
        return {'total': len(rows), 'items': [_revision_dict(r, with_content=False) for r in rows]}

    async def get_revision(
        self,
        db: AsyncSession,
        *,
        revision_id: int,
        viewer_owner_hasn_id: str,
        enterprise_id: int | None = None,
    ) -> dict[str, Any]:
        rev = await db.get(Revision, revision_id)
        if rev is None:
            raise errors.NotFoundError(msg='版本不存在')
        d = await self._get_alive(db, rev.design_system_id)
        self._assert_readable(d, viewer_owner_hasn_id, enterprise_id=enterprise_id)
        return _revision_dict(rev)

    async def compute_owner_revision(self, db: AsyncSession, *, owner_hasn_id: str) -> str:
        """owner 维度同步水位：可见集合（builtin ∪ owner）的 (id, content_hash) 有序聚合的 sha256。

        与 SKAU/PDC content-hash revision 同范式：内容不变 → revision 不变（幂等、可重放）。
        """
        rows = (
            await db.execute(
                select(DesignSystem.id, DesignSystem.content_hash)
                .where(
                    DesignSystem.deleted_time.is_(None),
                    or_(DesignSystem.is_builtin.is_(True), DesignSystem.owner_hasn_id == owner_hasn_id),
                )
                .order_by(DesignSystem.id)
            )
        ).all()
        blob = json.dumps([[r[0], r[1]] for r in rows], separators=(',', ':'))
        return hashlib.sha256(blob.encode('utf-8')).hexdigest()

    # ── 协作分身绑定（对齐 DECKBIND；详见 P9）────────────────────────────
    async def add_collaborator(
        self, db: AsyncSession, *, design_system_id: int, owner_hasn_id: str, agent_hasn_id: str
    ) -> dict[str, Any]:
        d = await self._get_alive(db, design_system_id)
        if d.owner_hasn_id != owner_hasn_id or d.is_builtin:
            raise errors.ForbiddenError(msg='无权为该设计系统绑定协作分身')
        existing = (
            await db.execute(
                select(Collaborator).where(
                    Collaborator.design_system_id == design_system_id,
                    Collaborator.agent_hasn_id == agent_hasn_id,
                )
            )
        ).scalar_one_or_none()
        if existing is not None:
            return {'id': existing.id, 'design_system_id': design_system_id, 'agent_hasn_id': agent_hasn_id}
        c = Collaborator(design_system_id=design_system_id, agent_hasn_id=agent_hasn_id, added_by=owner_hasn_id)
        db.add(c)
        await db.commit()
        await db.refresh(c)
        return {'id': c.id, 'design_system_id': design_system_id, 'agent_hasn_id': agent_hasn_id}

    async def list_collaborators(
        self, db: AsyncSession, *, design_system_id: int, viewer_owner_hasn_id: str
    ) -> dict[str, Any]:
        d = await self._get_alive(db, design_system_id)
        self._assert_readable(d, viewer_owner_hasn_id)
        rows = (
            (await db.execute(select(Collaborator).where(Collaborator.design_system_id == design_system_id)))
            .scalars()
            .all()
        )
        return {'total': len(rows), 'items': [{'id': r.id, 'agent_hasn_id': r.agent_hasn_id} for r in rows]}


design_system_service: DesignSystemService = DesignSystemService()
