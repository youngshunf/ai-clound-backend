"""deck 应用的 G6 资源类型适配器（doc32 §4·doc33 S2-6）。

deck 的 service 层本就有 subject+authorize 的**单一实现**（human 面共用），G6 在统一派发管线里
多判一次是**防御纵深**（代价一次 SELECT）：分身工具面（`app/mcp/tools/deck.py`）声明 `resource_access`
后，门在 ask 审批前先按同一 `resolve_effective_permission` 内核判权，确定性无权先拒、不打扰主人审批。

两类资源：
- `deck`：leaf id = deck_id，自身即 share 主体（无父链）。
- `deck_page`：leaf id = page_id，无独立 share（页权限继承所属 deck），父链 → 所属 deck。

deck 无维度②（没有「分身可动我哪些 deck」的白名单表），故不实现 `agent_domain_grant` 钩子。

层次纪律（doc32 §3）：适配器属应用层，可 import 本应用模型；平台门永不反向 import 本应用。
"""

from __future__ import annotations

import re

from typing import TYPE_CHECKING

import sqlalchemy as sa

from backend.app.hasn.service.authz.resource_registry import ResourceMeta, resource_kind_registry
from backend.app.hasn_deck.model import Deck, Page

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

# deck 页 HTML / 封面里内嵌的 canonical 资产引用 `hasn://asset/{id}`（渲染边界才换签名 URL）。
# collect_asset_ids 据此只收「确属该 deck」的资产 id，供资产投影门（doc32 §14）× 请求集求交放行。
_ASSET_URI_RE = re.compile(r'hasn://asset/([A-Za-z0-9_-]+)')


def _to_int(resource_id: str) -> int | None:
    """把权威 id 串转 int；畸形返回 None（门据此 404，绝不冒 500）。"""
    try:
        return int(resource_id)
    except (TypeError, ValueError):
        return None


class DeckResourceAdapter:
    """deck（演示文稿）资源适配器：resource_type='deck'，leaf id = deck_id。"""

    resource_type = 'deck'
    id_param_aliases = ('deck_id',)

    async def load_meta(self, db: AsyncSession, resource_id: str) -> ResourceMeta | None:
        deck_id = _to_int(resource_id)
        if deck_id is None:
            return None
        deck = (
            await db.execute(sa.select(Deck).where(Deck.id == deck_id, Deck.deleted_time.is_(None)))
        ).scalar_one_or_none()
        if deck is None:
            return None
        return ResourceMeta(
            resource_id=str(deck.id),
            owner_hasn_id=deck.owner_id,
            owner_scope=deck.owner_scope or 'personal',
            enterprise_id=deck.enterprise_id,
            visibility=deck.visibility or 'private',
            row=deck,
        )

    async def collect_asset_ids(self, db: AsyncSession, resource_id: str) -> set[str]:
        """收集本 deck 引用的全部内嵌私有资产 id（doc32 §14.3 契约·由 authorized_asset_ids 收集部分平移）。

        承载点穷尽：封面 `cover_asset_id` + 每页 `thumb_asset_id` + 每页 html 内嵌 `hasn://asset/{id}`。
        只收「确属该 deck」的 id——资产投影门据此 ∩ 请求集，防越权借一个 deck 签任意 asset。
        id 畸形 / deck 不存在(已删) → 空集，绝不冒异常（判权由门负责，本方法纯收集）。
        """
        deck_id = _to_int(resource_id)
        if deck_id is None:
            return set()
        # 轻量确认存在并取封面（只查一列，不整行 ORM load）；行不存在→空集。
        cover_row = (
            await db.execute(sa.select(Deck.cover_asset_id).where(Deck.id == deck_id, Deck.deleted_time.is_(None)))
        ).one_or_none()
        if cover_row is None:
            return set()
        ids: set[str] = set()
        if cover_row.cover_asset_id:
            ids.add(cover_row.cover_asset_id)
        rows = (
            await db.execute(
                sa.select(Page.html, Page.thumb_asset_id).where(Page.deck_id == deck_id, Page.deleted_time.is_(None))
            )
        ).all()
        for html, thumb_asset_id in rows:
            if thumb_asset_id:
                ids.add(thumb_asset_id)
            if html:
                ids.update(_ASSET_URI_RE.findall(html))
        return ids


class DeckPageResourceAdapter:
    """deck 页资源适配器：resource_type='deck_page'，leaf id = page_id，父链 → 所属 deck。

    页无独立 share（权限继承 deck），门跳过自身查询、只判父链（`has_own_shares` 缺省 False）。
    """

    resource_type = 'deck_page'
    id_param_aliases = ('page_id',)

    async def load_meta(self, db: AsyncSession, resource_id: str) -> ResourceMeta | None:
        page_id = _to_int(resource_id)
        if page_id is None:
            return None
        page = (
            await db.execute(sa.select(Page).where(Page.id == page_id, Page.deleted_time.is_(None)))
        ).scalar_one_or_none()
        if page is None:
            return None
        # 页无自身 share/可见面，档位纯继承 deck；owner 用冗余的 page.owner_id（=deck owner）。
        return ResourceMeta(
            resource_id=str(page.id),
            owner_hasn_id=page.owner_id,
            owner_scope='personal',
            enterprise_id=None,
            visibility='private',
            row=page,
        )

    async def resolve_parent(self, db: AsyncSession, resource_id: str) -> tuple[str, str] | None:
        page_id = _to_int(resource_id)
        if page_id is None:
            return None
        deck_id = (
            await db.execute(sa.select(Page.deck_id).where(Page.id == page_id, Page.deleted_time.is_(None)))
        ).scalar_one_or_none()
        if deck_id is None:
            return None
        return 'deck', str(deck_id)


def register() -> None:
    """把 deck 两类资源适配器注册进平台注册表（重名即抛，进程级只应跑一次）。"""
    for adapter in (DeckResourceAdapter(), DeckPageResourceAdapter()):
        if adapter.resource_type not in resource_kind_registry.registered_types():
            resource_kind_registry.register(adapter)


# 模块导入即注册（模块缓存保证进程内只跑一次）。平台启动经 ai_native_app_registry 触发；
# deck 平台工具模块 import 本模块同样触发，保证门用到 adapter 时它已在注册表。
register()
