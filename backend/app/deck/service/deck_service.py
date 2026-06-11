"""演示文稿（模块 17）云端业务服务：owner 隔离的 deck + page CRUD。

app scope（Owner JWT）与 agent scope（Agent JWT，owner=agent.owner_hasn_id）共用同一组方法，
仅 `owner_id` 来源不同——身份解析在 api 层完成，service 只认 owner_id 隔离键。
所有查询强制 `owner_id == owner_id AND deleted_time IS NULL`；跨 owner 访问按"不存在"处理（不泄露存在性）。
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from sqlalchemy import func, or_, select

from backend.app.deck.model import Deck, Page, StyleProfile
from backend.common.exception import errors
from backend.utils.timezone import timezone

# builtin（系统内置）风格的归属 owner 哨兵：对所有 owner 可见、不属于任何真实 owner。
BUILTIN_OWNER = 'system'

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

# 允许更新的字段白名单（挡住 owner_id/id/rev/deck_id 等被改）
_DECK_MUTABLE = (
    'title',
    'topic',
    'status',
    'language',
    'outline',
    'design_contract',
    'style_profile_id',
    'cover_asset_id',
)
_PAGE_MUTABLE = ('position', 'title', 'html', 'notes', 'layout_intent', 'status', 'render_state', 'thumb_asset_id')


def _deck_dict(d: Deck) -> dict[str, Any]:
    return {
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
        'rev': d.rev,
        'created_time': d.created_time,
        'updated_time': d.updated_time,
    }


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
    """owner 隔离的 deck/page 服务。所有方法第一参数 db，关键字 owner_id 为隔离键。"""

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
    ) -> dict[str, Any]:
        deck = Deck(
            owner_id=owner_id,
            title=title,
            topic=topic,
            status='draft',
            language=language,
            source=source,
            style_profile_id=style_profile_id,
        )
        db.add(deck)
        await db.flush()
        return _deck_dict(deck)

    @staticmethod
    async def list_decks(db: AsyncSession, *, owner_id: str, limit: int = 20, offset: int = 0) -> dict[str, Any]:
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
        return {'items': [_deck_dict(d) for d in rows], 'total': int(total)}

    @staticmethod
    async def _get_owned_deck(db: AsyncSession, *, owner_id: str, deck_id: int) -> Deck:
        deck = (
            await db.execute(
                select(Deck).where(Deck.id == deck_id, Deck.owner_id == owner_id, Deck.deleted_time.is_(None))
            )
        ).scalar_one_or_none()
        if deck is None:
            raise errors.NotFoundError(msg='演示文稿不存在')
        return deck

    @staticmethod
    async def get_deck(db: AsyncSession, *, owner_id: str, deck_id: int) -> dict[str, Any]:
        return _deck_dict(await DeckService._get_owned_deck(db, owner_id=owner_id, deck_id=deck_id))

    @staticmethod
    async def update_deck(db: AsyncSession, *, owner_id: str, deck_id: int, fields: dict[str, Any]) -> dict[str, Any]:
        deck = await DeckService._get_owned_deck(db, owner_id=owner_id, deck_id=deck_id)
        for key, value in fields.items():
            if key in _DECK_MUTABLE and value is not None:
                setattr(deck, key, value)
        deck.rev += 1
        await db.flush()
        return _deck_dict(deck)

    @staticmethod
    async def delete_deck(db: AsyncSession, *, owner_id: str, deck_id: int) -> None:
        deck = await DeckService._get_owned_deck(db, owner_id=owner_id, deck_id=deck_id)
        deck.deleted_time = timezone.now()
        await db.flush()

    # ---------- page ----------

    @staticmethod
    async def list_pages(db: AsyncSession, *, owner_id: str, deck_id: int) -> dict[str, Any]:
        await DeckService._get_owned_deck(db, owner_id=owner_id, deck_id=deck_id)  # owner 闸 + 存在性
        rows = (
            (
                await db.execute(
                    select(Page)
                    .where(Page.deck_id == deck_id, Page.owner_id == owner_id, Page.deleted_time.is_(None))
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
        owner_id: str,
        deck_id: int,
        position: int,
        title: str = '',
        html: str = '',
        notes: str | None = None,
        layout_intent: str | None = None,
        status: str = 'empty',
    ) -> dict[str, Any]:
        deck = await DeckService._get_owned_deck(db, owner_id=owner_id, deck_id=deck_id)
        page = Page(
            deck_id=deck_id,
            owner_id=owner_id,
            position=position,
            title=title,
            html=html,
            notes=notes,
            layout_intent=layout_intent,
            status=status,
        )
        db.add(page)
        deck.page_count += 1  # 冗余计数随未删页推进
        deck.rev += 1
        await db.flush()
        return _page_dict(page)

    @staticmethod
    async def _get_owned_page(db: AsyncSession, *, owner_id: str, page_id: int) -> Page:
        page = (
            await db.execute(
                select(Page).where(Page.id == page_id, Page.owner_id == owner_id, Page.deleted_time.is_(None))
            )
        ).scalar_one_or_none()
        if page is None:
            raise errors.NotFoundError(msg='幻灯片不存在')
        return page

    @staticmethod
    async def update_page(db: AsyncSession, *, owner_id: str, page_id: int, fields: dict[str, Any]) -> dict[str, Any]:
        page = await DeckService._get_owned_page(db, owner_id=owner_id, page_id=page_id)
        for key, value in fields.items():
            if key in _PAGE_MUTABLE and value is not None:
                setattr(page, key, value)
        page.rev += 1
        await db.flush()
        return _page_dict(page)

    @staticmethod
    async def delete_page(db: AsyncSession, *, owner_id: str, page_id: int) -> None:
        page = await DeckService._get_owned_page(db, owner_id=owner_id, page_id=page_id)
        page.deleted_time = timezone.now()
        deck = await DeckService._get_owned_deck(db, owner_id=owner_id, deck_id=page.deck_id)
        if deck.page_count > 0:
            deck.page_count -= 1
        deck.rev += 1
        await db.flush()

    @staticmethod
    async def list_style_profiles(db: AsyncSession, *, owner_id: str) -> dict[str, Any]:
        """可复用样式列表 = 系统内置（source='builtin'，对所有 owner 可见）∪ 该 owner 自定义。

        owner 隔离：仅返回 `owner_id == owner_id`（custom）或 `source == 'builtin'`（system）的未删行；
        绝不泄露其它 owner 的 custom 样式。builtin 优先、再按 slug 升序，给前端/分身稳定顺序。
        """
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
