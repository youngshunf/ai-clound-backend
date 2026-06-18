"""演示文稿（模块 17）云端业务服务：产物级协作（应用平台 v3）。

owner_id 仍是产物归属键，但访问控制不再是「owner==当前用户」硬隔离，而是
**有效权限判定**（§6.5，平台 `resource_share_service`）：owner / 企业 admin / 企业可见 / 显式共享 取 max。

- app scope（Owner JWT）：subject = 登录主人（human）。
- agent scope（Agent JWT）：subject = 分身（agent），背后主人 = agent.owner_hasn_id；分身**继承主人权限**，亦可被单独共享。
- 写操作（editor+）落审计署名；页级乐观锁复用 page.rev（expected_version 不匹配 → 409）。

业务系统型应用（获客/CRM）不走本服务，见 §6.7。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from sqlalchemy import func, or_, select

from backend.app.hasn.service.resource_share_service import rank, resource_share_service
from backend.app.hasn_deck.model import Deck, Page, StyleProfile
from backend.common.exception import errors
from backend.utils.timezone import timezone

# builtin（系统内置）风格的归属 owner 哨兵：对所有 owner 可见、不属于任何真实 owner。
BUILTIN_OWNER = 'system'
_RESOURCE_TYPE = 'deck'

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

# 允许更新的字段白名单（挡住 owner_id/id/rev/deck_id/owner_scope 等被改）
_DECK_MUTABLE = (
    'title',
    'topic',
    'status',
    'language',
    'outline',
    'design_contract',
    'style_profile_id',
    'cover_asset_id',
    'bound_agent_id',
)
_PAGE_MUTABLE = ('position', 'title', 'html', 'notes', 'layout_intent', 'status', 'render_state', 'thumb_asset_id')


@dataclass(frozen=True)
class Subject:
    """操作主体：人或分身（分身背后总有主人）。"""

    hasn_id: str
    kind: str  # 'human' | 'agent'
    owner_hasn_id: str  # 背后主人（human 时 == hasn_id）

    @staticmethod
    def human(hasn_id: str) -> Subject:
        return Subject(hasn_id=hasn_id, kind='human', owner_hasn_id=hasn_id)

    @staticmethod
    def agent(agent_hasn_id: str, owner_hasn_id: str) -> Subject:
        return Subject(hasn_id=agent_hasn_id, kind='agent', owner_hasn_id=owner_hasn_id)


def _deck_dict(d: Deck, *, my_permission: str | None = None, relation: str | None = None) -> dict[str, Any]:
    out = {
        'id': d.id,
        'owner_id': d.owner_id,
        'title': d.title,
        'topic': d.topic,
        'status': d.status,
        'language': d.language,
        'outline': d.outline,
        'design_contract': d.design_contract,
        'style_profile_id': d.style_profile_id,
        'page_count': d.page_count,
        'cover_asset_id': d.cover_asset_id,
        'source': d.source,
        'bound_agent_id': d.bound_agent_id,
        'owner_scope': d.owner_scope,
        'enterprise_id': d.enterprise_id,
        'visibility': d.visibility,
        'rev': d.rev,
        'created_time': d.created_time,
        'updated_time': d.updated_time,
    }
    if my_permission is not None:
        out['my_permission'] = my_permission
    if relation is not None:
        out['relation'] = relation
    return out


def _style_profile_dict(s: StyleProfile) -> dict[str, Any]:
    return {
        'slug': s.slug,
        'label': s.label,
        'description': s.description,
        'source': s.source,
        'design_contract': s.design_contract,
        'style_prompt': s.style_prompt,
        'rev': s.rev,
        'created_time': s.created_time,
        'updated_time': s.updated_time,
    }


def _page_dict(p: Page) -> dict[str, Any]:
    return {
        'id': p.id,
        'deck_id': p.deck_id,
        'owner_id': p.owner_id,
        'position': p.position,
        'title': p.title,
        'html': p.html,
        'notes': p.notes,
        'layout_intent': p.layout_intent,
        'status': p.status,
        'render_state': p.render_state,
        'thumb_asset_id': p.thumb_asset_id,
        'rev': p.rev,
        'created_time': p.created_time,
        'updated_time': p.updated_time,
    }


class DeckService:
    """产物级协作的 deck/page 服务。所有访问方法第一参数 db，subject 为操作主体。"""

    # ---------- 取 deck + 权限闸 ----------

    @staticmethod
    async def _get_deck(db: AsyncSession, deck_id: int) -> Deck:
        """按 id 取未删 deck（不做 owner 过滤；access 交给权限闸）。"""
        deck = (
            await db.execute(select(Deck).where(Deck.id == deck_id, Deck.deleted_time.is_(None)))
        ).scalar_one_or_none()
        if deck is None:
            raise errors.NotFoundError(msg='演示文稿不存在')
        return deck

    @staticmethod
    async def _effective_permission(db: AsyncSession, *, deck: Deck, subject: Subject) -> str:
        return await resource_share_service.resolve_effective_permission(
            db,
            subject_hasn_id=subject.hasn_id,
            subject_kind=subject.kind,
            subject_owner_hasn_id=subject.owner_hasn_id,
            resource_type=_RESOURCE_TYPE,
            resource_id=str(deck.id),
            resource_owner_hasn_id=deck.owner_id,
            resource_owner_scope=deck.owner_scope,
            resource_enterprise_id=deck.enterprise_id,
            resource_visibility=deck.visibility,
        )

    @staticmethod
    async def _authorize_deck(db: AsyncSession, *, deck: Deck, subject: Subject, need: str) -> str:
        """校验 subject 对 deck 至少有 need 权限；不足则报错（none→不存在不泄露，其它→无权限）。"""
        eff = await DeckService._effective_permission(db, deck=deck, subject=subject)
        if rank(eff) < rank(need):
            if rank(eff) == 0:
                raise errors.NotFoundError(msg='演示文稿不存在')
            raise errors.ForbiddenError(msg='没有该操作权限')
        return eff

    # ---------- deck ----------

    @staticmethod
    async def create_deck(
        db: AsyncSession,
        *,
        owner_id: str,
        title: str,
        topic: str | None = None,
        language: str = 'zh',
        source: str = 'manual',
        style_profile_id: str | None = None,
        bound_agent_id: str | None = None,
        owner_scope: str = 'personal',
        enterprise_id: int | None = None,
        visibility: str = 'private',
    ) -> dict[str, Any]:
        deck = Deck(
            owner_id=owner_id,
            title=title,
            topic=topic,
            status='draft',
            language=language,
            source=source,
            style_profile_id=style_profile_id,
            bound_agent_id=bound_agent_id,
            owner_scope=owner_scope,
            enterprise_id=enterprise_id,
            visibility=visibility,
        )
        db.add(deck)
        await db.flush()
        return _deck_dict(deck, my_permission='manager', relation='owner')

    @staticmethod
    async def list_decks(db: AsyncSession, *, owner_id: str, limit: int = 20, offset: int = 0) -> dict[str, Any]:
        """owner 自有 deck（「我的」）——保持原 owner 隔离语义。"""
        base = select(Deck).where(Deck.owner_id == owner_id, Deck.deleted_time.is_(None))
        total = (await db.execute(select(func.count()).select_from(base.subquery()))).scalar_one()
        rows = (
            (
                await db.execute(
                    base.order_by(Deck.updated_time.desc().nullslast(), Deck.id.desc()).limit(limit).offset(offset)
                )
            )
            .scalars()
            .all()
        )
        return {'items': [_deck_dict(d, my_permission='manager', relation='owner') for d in rows], 'total': int(total)}

    @staticmethod
    async def list_accessible_decks(
        db: AsyncSession, *, subject: Subject, limit: int = 50, offset: int = 0
    ) -> dict[str, Any]:
        """可访问 deck = 我拥有的 ∪ 共享给我的 ∪ 我企业可见的，每条带 relation + my_permission。"""
        human = subject.owner_hasn_id
        memberships = await resource_share_service.acting_human_memberships(db, human)
        member_enterprise_ids = {eid for eid, _ in memberships}

        # 1. 我拥有的
        owned = (
            (
                await db.execute(
                    select(Deck).where(Deck.owner_id == human, Deck.deleted_time.is_(None))
                )
            )
            .scalars()
            .all()
        )
        owned_ids = {d.id for d in owned}

        # 2. 共享给我的（直接给人 / 给我这个分身 / 给我企业）的 deck id
        shared_ids: set[str] = set(
            await resource_share_service.shared_resource_ids_for_human(db, resource_type=_RESOURCE_TYPE, human_hasn_id=human)
        )
        if subject.kind == 'agent':
            agent_share_ids = await DeckService._shared_ids_for_agent(db, agent_hasn_id=subject.hasn_id)
            shared_ids |= agent_share_ids

        # 3. 企业可见的 deck id
        ent_ids: set[int] = set()
        if member_enterprise_ids:
            rows = (
                (
                    await db.execute(
                        select(Deck.id).where(
                            Deck.deleted_time.is_(None),
                            Deck.owner_scope == 'enterprise',
                            Deck.visibility == 'enterprise',
                            Deck.enterprise_id.in_(member_enterprise_ids),
                        )
                    )
                )
                .scalars()
                .all()
            )
            ent_ids = set(rows)

        extra_ids = {int(i) for i in shared_ids if i.isdigit()} | ent_ids
        extra_ids -= owned_ids
        extra_decks: list[Deck] = []
        if extra_ids:
            extra_decks = list(
                (
                    await db.execute(select(Deck).where(Deck.id.in_(extra_ids), Deck.deleted_time.is_(None)))
                )
                .scalars()
                .all()
            )

        items: list[dict[str, Any]] = [
            _deck_dict(d, my_permission='manager', relation='owner') for d in owned
        ]
        for d in extra_decks:
            eff = await DeckService._effective_permission(db, deck=d, subject=subject)
            if rank(eff) == 0:
                continue
            relation = 'enterprise' if (d.id in ent_ids and str(d.id) not in shared_ids) else 'shared'
            items.append(_deck_dict(d, my_permission=eff, relation=relation))

        items.sort(key=lambda x: (x.get('updated_time') is None, x.get('updated_time')), reverse=True)
        total = len(items)
        return {'items': items[offset : offset + limit], 'total': total}

    @staticmethod
    async def _shared_ids_for_agent(db: AsyncSession, *, agent_hasn_id: str) -> set[str]:
        from backend.app.hasn.model import HasnResourceShare

        rows = (
            (
                await db.execute(
                    select(HasnResourceShare.resource_id).where(
                        HasnResourceShare.resource_type == _RESOURCE_TYPE,
                        HasnResourceShare.status == 'active',
                        HasnResourceShare.grantee_type == 'agent',
                        HasnResourceShare.grantee_id == agent_hasn_id,
                    )
                )
            )
            .scalars()
            .all()
        )
        return set(rows)

    @staticmethod
    async def get_deck(db: AsyncSession, *, subject: Subject, deck_id: int) -> dict[str, Any]:
        deck = await DeckService._get_deck(db, deck_id)
        eff = await DeckService._authorize_deck(db, deck=deck, subject=subject, need='viewer')
        relation = 'owner' if deck.owner_id == subject.owner_hasn_id else 'shared'
        return _deck_dict(deck, my_permission=eff, relation=relation)

    @staticmethod
    async def update_deck(db: AsyncSession, *, subject: Subject, deck_id: int, fields: dict[str, Any]) -> dict[str, Any]:
        deck = await DeckService._get_deck(db, deck_id)
        await DeckService._authorize_deck(db, deck=deck, subject=subject, need='editor')
        for key, value in fields.items():
            if key in _DECK_MUTABLE and value is not None:
                setattr(deck, key, value)
        deck.rev += 1
        await db.flush()
        return _deck_dict(deck)

    @staticmethod
    async def delete_deck(db: AsyncSession, *, subject: Subject, deck_id: int) -> None:
        deck = await DeckService._get_deck(db, deck_id)
        await DeckService._authorize_deck(db, deck=deck, subject=subject, need='manager')
        deck.deleted_time = timezone.now()
        await db.flush()

    # ---------- 共享管理（manager） ----------

    @staticmethod
    async def list_shares(db: AsyncSession, *, subject: Subject, deck_id: int) -> dict[str, Any]:
        deck = await DeckService._get_deck(db, deck_id)
        await DeckService._authorize_deck(db, deck=deck, subject=subject, need='manager')
        shares = await resource_share_service.list_shares(db, resource_type=_RESOURCE_TYPE, resource_id=str(deck_id))
        return {
            'deck_id': deck_id,
            'owner_scope': deck.owner_scope,
            'enterprise_id': deck.enterprise_id,
            'visibility': deck.visibility,
            'shares': shares,
        }

    @staticmethod
    async def set_visibility(
        db: AsyncSession, *, subject: Subject, deck_id: int, visibility: str, enterprise_id: int | None = None
    ) -> dict[str, Any]:
        deck = await DeckService._get_deck(db, deck_id)
        await DeckService._authorize_deck(db, deck=deck, subject=subject, need='manager')
        if visibility not in ('private', 'enterprise', 'link'):
            raise errors.RequestError(msg='非法可见性')
        if visibility == 'enterprise':
            target_ent = enterprise_id if enterprise_id is not None else deck.enterprise_id
            if target_ent is None:
                raise errors.ForbiddenError(msg='个人产物需先归属企业才能设为企业可见')
            memberships = await resource_share_service.acting_human_memberships(db, subject.owner_hasn_id)
            if target_ent not in {eid for eid, _ in memberships}:
                raise errors.ForbiddenError(msg='你不是该企业成员')
            deck.owner_scope = 'enterprise'
            deck.enterprise_id = target_ent
        deck.visibility = visibility
        deck.rev += 1
        await db.flush()
        return _deck_dict(deck)

    @staticmethod
    async def add_share(
        db: AsyncSession,
        *,
        subject: Subject,
        deck_id: int,
        grantee_type: str,
        grantee_id: str,
        permission: str,
    ) -> dict[str, Any]:
        if grantee_type not in ('human', 'agent', 'enterprise'):
            raise errors.RequestError(msg='P1 仅支持 human/agent/enterprise 协作者')
        if permission not in ('viewer', 'editor', 'manager'):
            raise errors.RequestError(msg='非法权限档')
        deck = await DeckService._get_deck(db, deck_id)
        await DeckService._authorize_deck(db, deck=deck, subject=subject, need='manager')
        return await resource_share_service.upsert_share(
            db,
            resource_type=_RESOURCE_TYPE,
            resource_id=str(deck_id),
            owner_hasn_id=deck.owner_id,
            grantee_type=grantee_type,
            grantee_id=grantee_id,
            permission=permission,
            granted_by=subject.hasn_id,
        )

    @staticmethod
    async def revoke_share(
        db: AsyncSession, *, subject: Subject, deck_id: int, grantee_type: str, grantee_id: str
    ) -> bool:
        deck = await DeckService._get_deck(db, deck_id)
        await DeckService._authorize_deck(db, deck=deck, subject=subject, need='manager')
        return await resource_share_service.revoke_share(
            db, resource_type=_RESOURCE_TYPE, resource_id=str(deck_id), grantee_type=grantee_type, grantee_id=grantee_id
        )

    # ---------- page ----------

    @staticmethod
    async def _get_page(db: AsyncSession, page_id: int) -> Page:
        page = (
            await db.execute(select(Page).where(Page.id == page_id, Page.deleted_time.is_(None)))
        ).scalar_one_or_none()
        if page is None:
            raise errors.NotFoundError(msg='幻灯片不存在')
        return page

    @staticmethod
    async def list_pages(db: AsyncSession, *, subject: Subject, deck_id: int) -> dict[str, Any]:
        deck = await DeckService._get_deck(db, deck_id)
        await DeckService._authorize_deck(db, deck=deck, subject=subject, need='viewer')
        rows = (
            (
                await db.execute(
                    select(Page)
                    .where(Page.deck_id == deck_id, Page.deleted_time.is_(None))
                    .order_by(Page.position.asc())
                )
            )
            .scalars()
            .all()
        )
        return {'items': [_page_dict(p) for p in rows], 'total': len(rows)}

    @staticmethod
    async def create_page(
        db: AsyncSession,
        *,
        subject: Subject,
        deck_id: int,
        position: int,
        title: str = '',
        html: str = '',
        notes: str | None = None,
        layout_intent: str | None = None,
        status: str = 'empty',
    ) -> dict[str, Any]:
        deck = await DeckService._get_deck(db, deck_id)
        await DeckService._authorize_deck(db, deck=deck, subject=subject, need='editor')
        page = Page(
            deck_id=deck_id,
            owner_id=deck.owner_id,  # 页归属随 deck（共享编辑者不改归属）
            position=position,
            title=title,
            html=html,
            notes=notes,
            layout_intent=layout_intent,
            status=status,
        )
        db.add(page)
        deck.page_count += 1
        deck.rev += 1
        await db.flush()
        return _page_dict(page)

    @staticmethod
    async def _move_page_to_position(db: AsyncSession, *, page: Page, new_position: int) -> None:
        """把 page 移到 new_position；若该位被同 deck 另一未删页占用则与之原子交换（临时负位两段式）。"""
        old_position = page.position
        occupant = (
            await db.execute(
                select(Page).where(
                    Page.deck_id == page.deck_id,
                    Page.deleted_time.is_(None),
                    Page.position == new_position,
                    Page.id != page.id,
                )
            )
        ).scalar_one_or_none()
        if occupant is None:
            page.position = new_position
            return
        page.position = -(page.id + 1)
        await db.flush()
        occupant.position = old_position
        occupant.rev += 1
        await db.flush()
        page.position = new_position

    @staticmethod
    async def update_page(
        db: AsyncSession,
        *,
        subject: Subject,
        page_id: int,
        fields: dict[str, Any],
        expected_version: int | None = None,
    ) -> dict[str, Any]:
        page = await DeckService._get_page(db, page_id)
        deck = await DeckService._get_deck(db, page.deck_id)
        await DeckService._authorize_deck(db, deck=deck, subject=subject, need='editor')
        # 页级乐观锁（§6.6）：复用 page.rev，expected_version 不匹配则拒绝
        if expected_version is not None and int(page.rev) != int(expected_version):
            raise errors.ConflictError(msg='STALE_VERSION：该页已被他人更新，请刷新后重试')
        new_position = fields.get('position')
        if new_position is not None and new_position != page.position:
            await DeckService._move_page_to_position(db, page=page, new_position=new_position)
        for key, value in fields.items():
            if key in _PAGE_MUTABLE and key != 'position' and value is not None:
                setattr(page, key, value)
        page.rev += 1
        await db.flush()
        return _page_dict(page)

    @staticmethod
    async def delete_page(db: AsyncSession, *, subject: Subject, page_id: int) -> None:
        page = await DeckService._get_page(db, page_id)
        deck = await DeckService._get_deck(db, page.deck_id)
        await DeckService._authorize_deck(db, deck=deck, subject=subject, need='editor')
        page.deleted_time = timezone.now()
        if deck.page_count > 0:
            deck.page_count -= 1
        deck.rev += 1
        await db.flush()

    # ---------- style profiles（owner 隔离不变） ----------

    @staticmethod
    async def list_style_profiles(db: AsyncSession, *, owner_id: str) -> dict[str, Any]:
        """可复用样式列表 = 系统内置（builtin，对所有 owner 可见）∪ 该 owner 自定义。"""
        stmt = (
            select(StyleProfile)
            .where(
                StyleProfile.deleted_time.is_(None),
                or_(StyleProfile.owner_id == owner_id, StyleProfile.source == 'builtin'),
            )
            .order_by((StyleProfile.source == 'builtin').desc(), StyleProfile.slug.asc())
        )
        rows = (await db.execute(stmt)).scalars().all()
        return {'items': [_style_profile_dict(s) for s in rows], 'total': len(rows)}


deck_service = DeckService()
