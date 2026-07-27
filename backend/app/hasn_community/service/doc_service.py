"""文档系统服务（设计文档 17）——文集 + 多级目录树 + 目录级可见性 / 密码临时访问。

物化路径约定（含自身）：根节点 path='/dn_a'，其子 '/dn_a/dn_b'，孙 '/dn_a/dn_b/dn_c'。
depth = 路径段数 - 1（根下一级=0）。子树查询：path = X.path OR path LIKE X.path||'/%'。
有效可见性沿 parent 链向上取最近非空，根回退 space.default_visibility。
密码临时访问：unlock 比对 password_hash → 签发无状态 grant_token（载荷带 pwd_version，改密失效）。
"""

from __future__ import annotations

from datetime import timedelta
from typing import TYPE_CHECKING, Any

import bcrypt

from jose import jwt
from sqlalchemy import func, select, update

from backend.app.hasn_community.model import HasnArticles, HasnDocNodes, HasnDocSpaces
from backend.app.hasn_core import HasnAgents, HasnHumans
from backend.common.exception import errors
from backend.core.conf import settings
from backend.database.db import uuid4_str
from backend.utils.timezone import timezone

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

_VALID_NODE_VIS = {'public', 'private', 'password'}
_GRANT_TTL_HOURS = 2
_GRANT_TYP = 'doc_grant'


def _hash_pw(pw: str) -> str:
    return bcrypt.hashpw(pw.encode(), bcrypt.gensalt()).decode()


def _verify_pw(pw: str, hashed: str) -> bool:
    try:
        return bcrypt.checkpw(pw.encode(), hashed.encode())
    except Exception:
        return False


def _slugify(title: str) -> str:
    import re
    base = re.sub(r'[^a-z0-9]+', '-', (title or '').lower()).strip('-')
    return base[:100] if base else f'ds-{uuid4_str()[:8]}'


class DocService:
    # ---------- 文集 CRUD ----------

    @staticmethod
    async def _get_space(db: AsyncSession, ident: str) -> HasnDocSpaces | None:
        col = HasnDocSpaces.space_id if ident.startswith('ds_') else HasnDocSpaces.slug
        return (await db.execute(select(HasnDocSpaces).where(col == ident, HasnDocSpaces.status != 'deleted'))).scalars().first()

    @staticmethod
    def _space_dict(s: HasnDocSpaces) -> dict[str, Any]:
        return {
            'space_id': s.space_id, 'owner_hasn_id': s.owner_hasn_id, 'author_type': s.author_type,
            'author_hasn_id': s.author_hasn_id, 'title': s.title, 'slug': s.slug, 'description': s.description,
            'cover_url': s.cover_url, 'default_visibility': s.default_visibility, 'node_count': s.node_count,
            'article_count': s.article_count, 'status': s.status,
            'has_password': bool(s.default_password_hash),
        }

    @staticmethod
    async def create_space(
        db: AsyncSession, *, owner_hasn_id: str, author_type: str, author_hasn_id: str, owner_user_id: int,
        title: str, description: str | None = None, cover_url: str | None = None,
        default_visibility: str = 'private', default_password: str | None = None,
        workspace_kind: str = 'personal', workspace_id: str | None = None,
    ) -> dict[str, Any]:
        title = (title or '').strip()
        if not title:
            raise errors.RequestError(msg='文集标题不能为空')
        if default_visibility not in _VALID_NODE_VIS:
            raise errors.RequestError(msg='default_visibility 非法')
        space_id = f'ds_{uuid4_str()[:12]}'
        slug = _slugify(title)
        # owner 下 slug 唯一
        if (await db.execute(select(HasnDocSpaces.id).where(HasnDocSpaces.owner_hasn_id == owner_hasn_id, HasnDocSpaces.slug == slug))).first():
            slug = f'{slug[:90]}-{uuid4_str()[:6]}'
        pw_hash = _hash_pw(default_password) if (default_visibility == 'password' and default_password) else None
        space = HasnDocSpaces(
            space_id=space_id, owner_hasn_id=owner_hasn_id, author_type=author_type, author_hasn_id=author_hasn_id,
            origin_workspace_kind=workspace_kind, origin_workspace_id=workspace_id or str(owner_user_id),
            title=title, slug=slug, description=description, cover_url=cover_url,
            default_visibility=default_visibility, default_password_hash=pw_hash, node_count=0, article_count=0, status='active',
        )
        db.add(space)
        await db.flush()
        return DocService._space_dict(space)

    @staticmethod
    async def get_space(db: AsyncSession, ident: str, *, viewer_hasn_id: str | None = None, public_only: bool = False) -> dict[str, Any]:
        s = await DocService._get_space(db, ident)
        if not s or s.status != 'active':
            raise errors.NotFoundError(msg='文集不存在')
        if public_only and s.default_visibility == 'private' and viewer_hasn_id != s.owner_hasn_id:
            # 私有文集根：仅在存在公开子树时可被发现；这里仍返回壳（标题/描述），树接口负责裁剪
            pass
        d = DocService._space_dict(s)
        d['is_owner'] = viewer_hasn_id == s.owner_hasn_id
        return d

    @staticmethod
    async def list_mine(db: AsyncSession, *, owner_hasn_id: str) -> list[dict[str, Any]]:
        rows = (
            await db.execute(
                select(HasnDocSpaces).where(HasnDocSpaces.owner_hasn_id == owner_hasn_id, HasnDocSpaces.status == 'active').order_by(HasnDocSpaces.created_time.desc())
            )
        ).scalars().all()
        return [DocService._space_dict(s) for s in rows]

    @staticmethod
    async def _author_info(db: AsyncSession, author_type: str, author_hasn_id: str, owner_hasn_id: str) -> dict[str, Any]:
        """解析文集作者展示信息（human/agent）。Agent 作者附带主人昵称。"""
        info: dict[str, Any] = {'hasn_id': author_hasn_id, 'type': author_type}
        if author_type == 'agent':
            agent = (await db.execute(select(HasnAgents).where(HasnAgents.hasn_id == author_hasn_id))).scalars().first()
            info['display_name'] = (agent.display_name if agent else None) or author_hasn_id
            info['avatar'] = agent.avatar if agent else None
            owner = (await db.execute(select(HasnHumans).where(HasnHumans.hasn_id == owner_hasn_id))).scalars().first()
            if owner:
                info['owner'] = {'hasn_id': owner.hasn_id, 'display_name': owner.nickname or owner.hasn_id}
        else:
            human = (await db.execute(select(HasnHumans).where(HasnHumans.hasn_id == author_hasn_id))).scalars().first()
            info['display_name'] = (human.nickname if human else None) or author_hasn_id
            info['avatar'] = human.avatar if human else None
        return info

    @staticmethod
    async def discover_public(db: AsyncSession, *, cursor: str | None = None, limit: int = 20) -> dict[str, Any]:
        """发现公开文集：default_visibility='public' 且 active，按创建时间倒序，附作者信息。

        :return: {items, next_cursor}
        """
        offset = int(cursor) if cursor else 0
        rows = (
            await db.execute(
                select(HasnDocSpaces)
                .where(HasnDocSpaces.status == 'active', HasnDocSpaces.default_visibility == 'public')
                .order_by(HasnDocSpaces.created_time.desc())
                .offset(offset)
                .limit(limit + 1)
            )
        ).scalars().all()
        has_more = len(rows) > limit
        rows = rows[:limit]
        items = []
        for s in rows:
            d = DocService._space_dict(s)
            d['author'] = await DocService._author_info(db, s.author_type, s.author_hasn_id, s.owner_hasn_id)
            items.append(d)
        return {'items': items, 'next_cursor': str(offset + limit) if has_more else None}

    @staticmethod
    async def _assert_space_owner(db: AsyncSession, ident: str, actor_hasn_id: str) -> HasnDocSpaces:
        s = await DocService._get_space(db, ident)
        if not s:
            raise errors.NotFoundError(msg='文集不存在')
        if s.owner_hasn_id != actor_hasn_id:
            raise errors.ForbiddenError(msg='仅文集主人可操作')
        return s

    @staticmethod
    async def update_space(db: AsyncSession, *, ident: str, actor_hasn_id: str, **fields: Any) -> dict[str, Any]:
        s = await DocService._assert_space_owner(db, ident, actor_hasn_id)
        for k in ('title', 'description', 'cover_url'):
            if fields.get(k) is not None:
                setattr(s, k, fields[k])
        if fields.get('default_visibility') is not None:
            if fields['default_visibility'] not in _VALID_NODE_VIS:
                raise errors.RequestError(msg='default_visibility 非法')
            s.default_visibility = fields['default_visibility']
        if fields.get('default_password') is not None:
            s.default_password_hash = _hash_pw(fields['default_password']) if fields['default_password'] else None
        await db.flush()
        return DocService._space_dict(s)

    @staticmethod
    async def delete_space(db: AsyncSession, *, ident: str, actor_hasn_id: str) -> dict[str, Any]:
        s = await DocService._assert_space_owner(db, ident, actor_hasn_id)
        s.status = 'deleted'
        await db.execute(update(HasnDocNodes).where(HasnDocNodes.space_id == s.space_id).values(status='deleted'))
        await db.flush()
        return {'space_id': s.space_id, 'status': 'deleted'}

    # ---------- 节点 CRUD + 树维护 ----------

    @staticmethod
    def _node_dict(n: HasnDocNodes) -> dict[str, Any]:
        return {
            'node_id': n.node_id, 'space_id': n.space_id, 'parent_node_id': n.parent_node_id, 'node_type': n.node_type,
            'title': n.title, 'article_id': n.article_id, 'sort_order': n.sort_order, 'depth': n.depth, 'path': n.path,
            'visibility': n.visibility, 'has_password': bool(n.password_hash), 'pwd_version': n.pwd_version,
        }

    @staticmethod
    async def create_node(
        db: AsyncSession, *, space_id: str, actor_hasn_id: str, node_type: str, title: str,
        parent_node_id: str | None = None, article_id: str | None = None,
        visibility: str | None = None, password: str | None = None, sort_order: int | None = None,
        allow_visibility: bool = True,
    ) -> dict[str, Any]:
        s = await DocService._assert_space_owner(db, space_id, actor_hasn_id)
        if node_type not in ('directory', 'article'):
            raise errors.RequestError(msg='node_type 仅支持 directory/article')
        if node_type == 'directory' and article_id:
            raise errors.RequestError(msg='目录节点不能挂文章')
        if node_type == 'article' and not article_id:
            raise errors.RequestError(msg='文章节点必须指定 article_id')
        parent = None
        if parent_node_id:
            parent = (await db.execute(select(HasnDocNodes).where(HasnDocNodes.node_id == parent_node_id, HasnDocNodes.space_id == s.space_id, HasnDocNodes.status == 'active'))).scalars().first()
            if not parent:
                raise errors.NotFoundError(msg='父节点不存在')
            if parent.node_type != 'directory':
                raise errors.RequestError(msg='只能在目录下建子节点')
            if parent.depth >= 5:
                raise errors.RequestError(msg='目录层级超过上限（6 级）')
        node_id = f'dn_{uuid4_str()[:12]}'
        depth = (parent.depth + 1) if parent else 0
        path = (parent.path + '/' + node_id) if parent else ('/' + node_id)
        if sort_order is None:
            mx = (await db.execute(select(func.coalesce(func.max(HasnDocNodes.sort_order), -1)).where(HasnDocNodes.space_id == s.space_id, HasnDocNodes.parent_node_id.is_(parent_node_id) if parent_node_id is None else HasnDocNodes.parent_node_id == parent_node_id))).scalar()
            sort_order = int(mx if mx is not None else -1) + 1
        vis = None
        pw_hash = None
        if allow_visibility and visibility is not None:
            if visibility not in _VALID_NODE_VIS:
                raise errors.RequestError(msg='visibility 非法')
            vis = visibility
            if visibility == 'password' and password:
                pw_hash = _hash_pw(password)
        node = HasnDocNodes(
            node_id=node_id, space_id=s.space_id, parent_node_id=parent_node_id, node_type=node_type, title=title,
            article_id=article_id, sort_order=sort_order, depth=depth, path=path, visibility=vis, password_hash=pw_hash,
            pwd_version=0, status='active',
        )
        db.add(node)
        await db.execute(update(HasnDocSpaces).where(HasnDocSpaces.space_id == s.space_id).values(
            node_count=HasnDocSpaces.node_count + 1,
            article_count=HasnDocSpaces.article_count + (1 if node_type == 'article' else 0),
        ))
        await db.flush()
        return DocService._node_dict(node)

    @staticmethod
    async def _get_node(db: AsyncSession, node_id: str) -> HasnDocNodes | None:
        return (await db.execute(select(HasnDocNodes).where(HasnDocNodes.node_id == node_id, HasnDocNodes.status == 'active'))).scalars().first()

    @staticmethod
    async def update_node(db: AsyncSession, *, node_id: str, actor_hasn_id: str, title: str | None = None, visibility: str | None = None, password: str | None = None) -> dict[str, Any]:
        n = await DocService._get_node(db, node_id)
        if not n:
            raise errors.NotFoundError(msg='节点不存在')
        await DocService._assert_space_owner(db, n.space_id, actor_hasn_id)
        if title is not None:
            n.title = title
        if visibility is not None:
            if visibility not in _VALID_NODE_VIS and visibility != 'inherit':
                raise errors.RequestError(msg='visibility 非法')
            n.visibility = None if visibility == 'inherit' else visibility
            if n.visibility != 'password':
                n.password_hash = None
        if password is not None:
            n.visibility = 'password'
            n.password_hash = _hash_pw(password) if password else None
            n.pwd_version = n.pwd_version + 1  # 改密 bump，旧 grant_token 失效
        await db.flush()
        return DocService._node_dict(n)

    @staticmethod
    async def _subtree(db: AsyncSession, node: HasnDocNodes) -> list[HasnDocNodes]:
        return list((
            await db.execute(
                select(HasnDocNodes).where(
                    HasnDocNodes.space_id == node.space_id, HasnDocNodes.status == 'active',
                    (HasnDocNodes.path == node.path) | (HasnDocNodes.path.like(node.path + '/%')),
                )
            )
        ).scalars().all())

    @staticmethod
    async def move_node(db: AsyncSession, *, node_id: str, actor_hasn_id: str, new_parent_node_id: str | None) -> dict[str, Any]:
        n = await DocService._get_node(db, node_id)
        if not n:
            raise errors.NotFoundError(msg='节点不存在')
        await DocService._assert_space_owner(db, n.space_id, actor_hasn_id)
        old_path = n.path
        old_depth = n.depth
        new_parent = None
        if new_parent_node_id:
            new_parent = await DocService._get_node(db, new_parent_node_id)
            if not new_parent or new_parent.space_id != n.space_id:
                raise errors.NotFoundError(msg='目标父节点不存在')
            if new_parent.node_type != 'directory':
                raise errors.RequestError(msg='只能挂到目录下')
            # 防环：新父不能是自己或自己的后代
            if new_parent.path == old_path or new_parent.path.startswith(old_path + '/'):
                raise errors.RequestError(msg='不能移动到自身子树内')
        new_self_path = (new_parent.path + '/' + n.node_id) if new_parent else ('/' + n.node_id)
        new_depth = (new_parent.depth + 1) if new_parent else 0
        delta = new_depth - old_depth
        subtree = await DocService._subtree(db, n)
        for d in subtree:
            d.path = new_self_path + d.path[len(old_path):]
            d.depth = d.depth + delta
        n.parent_node_id = new_parent_node_id
        await db.flush()
        return DocService._node_dict(n)

    @staticmethod
    async def reorder_nodes(db: AsyncSession, *, actor_hasn_id: str, ordered_node_ids: list[str]) -> dict[str, Any]:
        if not ordered_node_ids:
            return {'reordered': 0}
        first = await DocService._get_node(db, ordered_node_ids[0])
        if not first:
            raise errors.NotFoundError(msg='节点不存在')
        await DocService._assert_space_owner(db, first.space_id, actor_hasn_id)
        for i, nid in enumerate(ordered_node_ids):
            await db.execute(update(HasnDocNodes).where(HasnDocNodes.node_id == nid, HasnDocNodes.space_id == first.space_id).values(sort_order=i))
        await db.flush()
        return {'reordered': len(ordered_node_ids)}

    @staticmethod
    async def delete_node(db: AsyncSession, *, node_id: str, actor_hasn_id: str, cascade: bool = True) -> dict[str, Any]:
        n = await DocService._get_node(db, node_id)
        if not n:
            raise errors.NotFoundError(msg='节点不存在')
        await DocService._assert_space_owner(db, n.space_id, actor_hasn_id)
        if cascade:
            subtree = await DocService._subtree(db, n)
            ids = [d.node_id for d in subtree]
            arts = sum(1 for d in subtree if d.node_type == 'article')
            await db.execute(update(HasnDocNodes).where(HasnDocNodes.node_id.in_(ids)).values(status='deleted'))
            await db.execute(update(HasnDocSpaces).where(HasnDocSpaces.space_id == n.space_id).values(
                node_count=func.greatest(HasnDocSpaces.node_count - len(ids), 0),
                article_count=func.greatest(HasnDocSpaces.article_count - arts, 0),
            ))
        else:
            # 上提子节点到本节点的父
            children = (await db.execute(select(HasnDocNodes).where(HasnDocNodes.parent_node_id == n.node_id, HasnDocNodes.status == 'active'))).scalars().all()
            for ch in children:
                await DocService.move_node(db, node_id=ch.node_id, actor_hasn_id=actor_hasn_id, new_parent_node_id=n.parent_node_id)
            n.status = 'deleted'
            await db.execute(update(HasnDocSpaces).where(HasnDocSpaces.space_id == n.space_id).values(node_count=func.greatest(HasnDocSpaces.node_count - 1, 0)))
        await db.flush()
        return {'node_id': node_id, 'status': 'deleted'}

    # ---------- 有效可见性 + 密码 ----------

    @staticmethod
    def _effective_governing(node: HasnDocNodes, by_id: dict[str, HasnDocNodes]) -> HasnDocNodes | None:
        """沿 parent 链向上取最近一个 visibility 非空的节点（含自身）。"""
        cur: HasnDocNodes | None = node
        while cur is not None:
            if cur.visibility:
                return cur
            cur = by_id.get(cur.parent_node_id) if cur.parent_node_id else None
        return None

    @staticmethod
    def _grant_decode(token: str) -> dict[str, Any] | None:
        try:
            payload = jwt.decode(token, settings.TOKEN_SECRET_KEY, algorithms=[settings.TOKEN_ALGORITHM])
            if payload.get('typ') != _GRANT_TYP:
                return None
            return payload
        except Exception:
            return None

    @staticmethod
    async def unlock(db: AsyncSession, *, node_id: str, password: str) -> dict[str, Any]:
        n = await DocService._get_node(db, node_id)
        if not n:
            raise errors.NotFoundError(msg='节点不存在')
        space = (await db.execute(select(HasnDocSpaces).where(HasnDocSpaces.space_id == n.space_id))).scalars().first()
        nodes = (await db.execute(select(HasnDocNodes).where(HasnDocNodes.space_id == n.space_id, HasnDocNodes.status == 'active'))).scalars().all()
        by_id = {x.node_id: x for x in nodes}
        gov = DocService._effective_governing(n, by_id)
        if gov and gov.visibility == 'password':
            if not gov.password_hash or not _verify_pw(password, gov.password_hash):
                raise errors.ForbiddenError(msg='密码错误')
            subtree_path, pwd_version = gov.path, gov.pwd_version
        elif (not gov) and space and space.default_visibility == 'password':
            if not space.default_password_hash or not _verify_pw(password, space.default_password_hash):
                raise errors.ForbiddenError(msg='密码错误')
            subtree_path, pwd_version = '', 0
        else:
            raise errors.RequestError(msg='该节点无需密码')
        exp = timezone.now() + timedelta(hours=_GRANT_TTL_HOURS)
        token = jwt.encode(
            {'typ': _GRANT_TYP, 'space_id': n.space_id, 'subtree_path': subtree_path, 'pwd_version': pwd_version, 'exp': exp},
            settings.TOKEN_SECRET_KEY, settings.TOKEN_ALGORITHM,
        )
        return {'grant_token': token, 'expires_in': _GRANT_TTL_HOURS * 3600, 'space_id': n.space_id}

    @staticmethod
    def _node_unlocked(node: HasnDocNodes, gov: HasnDocNodes | None, space: HasnDocSpaces, grants: list[dict[str, Any]]) -> bool:
        """该 password 节点是否被有效 grant_token 解锁。"""
        target_path = gov.path if gov else ''
        target_ver = gov.pwd_version if gov else 0
        for g in grants:
            if g.get('space_id') != space.space_id:
                continue
            gp = g.get('subtree_path', '')
            # token 覆盖 target（target 在 token 子树内或同一节点）
            covers = (gp == target_path) or (target_path.startswith(gp + '/') if gp else True)
            if covers and g.get('pwd_version') == target_ver:
                return True
        return False

    # ---------- 树渲染（按 viewer 裁剪） ----------

    @staticmethod
    async def get_tree(
        db: AsyncSession, *, space_ident: str, viewer_hasn_id: str | None = None, public_only: bool = False,
        focus_article_id: str | None = None, grant_tokens: list[str] | None = None,
    ) -> dict[str, Any]:
        s = await DocService._get_space(db, space_ident)
        if not s or s.status != 'active':
            raise errors.NotFoundError(msg='文集不存在')
        is_owner = (not public_only) and viewer_hasn_id == s.owner_hasn_id
        nodes = (await db.execute(select(HasnDocNodes).where(HasnDocNodes.space_id == s.space_id, HasnDocNodes.status == 'active').order_by(HasnDocNodes.sort_order))).scalars().all()
        by_id = {n.node_id: n for n in nodes}
        grants = [g for g in (DocService._grant_decode(t) for t in (grant_tokens or [])) if g]

        def effective_vis(n: HasnDocNodes) -> str:
            gov = DocService._effective_governing(n, by_id)
            if gov is not None and gov.visibility is not None:
                return gov.visibility
            return s.default_visibility

        def visible(n: HasnDocNodes) -> bool:
            if is_owner:
                return True
            gov = DocService._effective_governing(n, by_id)
            ev = gov.visibility if gov else s.default_visibility
            if ev == 'public':
                return True
            if ev == 'password':
                return DocService._node_unlocked(n, gov, s, grants)
            return False  # private

        # 收集可见文章叶子（树序）用于上/下一篇
        children: dict[str | None, list[HasnDocNodes]] = {}
        for n in nodes:
            children.setdefault(n.parent_node_id, []).append(n)
        for k in children:
            children[k].sort(key=lambda x: (x.sort_order, x.id))

        ordered_article_leaves: list[str] = []

        def build(parent_id: str | None) -> list[dict[str, Any]]:
            out = []
            for n in children.get(parent_id, []):
                ev = effective_vis(n)
                if visible(n):
                    node_payload = {
                        **DocService._node_dict(n), 'effective_visibility': ev, 'locked': False,
                        'children': build(n.node_id),
                    }
                    if n.node_type == 'article' and n.article_id:
                        ordered_article_leaves.append(n.article_id)
                    out.append(node_payload)
                elif ev == 'password':
                    # 锁定占位：保留"此处有受保护内容"，不泄露子树与文章
                    out.append({'node_id': n.node_id, 'node_type': n.node_type, 'title': '🔒 受保护内容', 'effective_visibility': 'password', 'locked': True, 'children': []})
                # private：整剪，不泄露标题
            return out

        tree = build(None)
        result: dict[str, Any] = {'space': DocService._space_dict(s), 'is_owner': is_owner, 'tree': tree}
        if focus_article_id and focus_article_id in ordered_article_leaves:
            idx = ordered_article_leaves.index(focus_article_id)
            result['focus'] = {
                'article_id': focus_article_id,
                'prev_article_id': ordered_article_leaves[idx - 1] if idx > 0 else None,
                'next_article_id': ordered_article_leaves[idx + 1] if idx < len(ordered_article_leaves) - 1 else None,
            }
        return result

    @staticmethod
    async def get_article_via_space(db: AsyncSession, *, space_ident: str, article_id: str, viewer_hasn_id: str | None = None, public_only: bool = False, grant_tokens: list[str] | None = None) -> dict[str, Any]:
        """通过文集读文章，受节点有效可见性约束。"""
        s = await DocService._get_space(db, space_ident)
        if not s:
            raise errors.NotFoundError(msg='文集不存在')
        node = (await db.execute(select(HasnDocNodes).where(HasnDocNodes.space_id == s.space_id, HasnDocNodes.article_id == article_id, HasnDocNodes.status == 'active'))).scalars().first()
        if not node:
            raise errors.NotFoundError(msg='文章不在该文集中')
        nodes = (await db.execute(select(HasnDocNodes).where(HasnDocNodes.space_id == s.space_id, HasnDocNodes.status == 'active'))).scalars().all()
        by_id = {n.node_id: n for n in nodes}
        is_owner = (not public_only) and viewer_hasn_id == s.owner_hasn_id
        if not is_owner:
            gov = DocService._effective_governing(node, by_id)
            ev = gov.visibility if gov else s.default_visibility
            grants = [g for g in (DocService._grant_decode(t) for t in (grant_tokens or [])) if g]
            if ev == 'private' or (ev == 'password' and not DocService._node_unlocked(node, gov, s, grants)):
                raise errors.ForbiddenError(msg='无权访问该文档（私有或未解锁）')
        article = (await db.execute(select(HasnArticles).where(HasnArticles.article_id == article_id))).scalars().first()
        if not article:
            raise errors.NotFoundError(msg='文章不存在')
        return {
            'article_id': article.article_id, 'title': article.title, 'summary': article.summary,
            'content': article.content, 'cover_url': article.cover_url, 'tags': article.tags or [],
            'author': {'hasn_id': article.author_hasn_id, 'type': article.author_type},
            'published_time': article.published_time.isoformat() if article.published_time else None,
            'space_id': s.space_id, 'node_id': node.node_id,
        }

    # ---------- 发文落位（供发布汇聚调用） ----------

    @staticmethod
    async def place_article(
        db: AsyncSession, *, article_id: str, article_title: str, actor_hasn_id: str, owner_user_id: int,
        author_type: str, author_hasn_id: str, placement: dict[str, Any], allow_visibility: bool = True,
    ) -> dict[str, Any]:
        """建/复用目录链 → 建 article 叶子指向文章 → 设叶子可见性/密码。"""
        space_id = placement.get('space_id')
        if not space_id and placement.get('new_space'):
            ns = placement['new_space']
            created = await DocService.create_space(
                db, owner_hasn_id=actor_hasn_id, author_type=author_type, author_hasn_id=author_hasn_id, owner_user_id=owner_user_id,
                title=ns.get('title') or article_title, description=ns.get('description'), cover_url=ns.get('cover_url'),
                default_visibility=ns.get('default_visibility', 'private'),
            )
            space_id = created['space_id']
        if not space_id:
            raise errors.RequestError(msg='doc_placement 需要 space_id 或 new_space')
        s = await DocService._assert_space_owner(db, space_id, actor_hasn_id)
        parent_node_id = placement.get('parent_node_id')
        # 即时多级目录（自上而下，幂等：同 space 同 parent 同名目录复用）
        for dir_name in (placement.get('new_dirs') or []):
            name = (dir_name or '').strip()
            if not name:
                continue
            existing = (
                await db.execute(
                    select(HasnDocNodes).where(
                        HasnDocNodes.space_id == s.space_id, HasnDocNodes.node_type == 'directory', HasnDocNodes.title == name,
                        HasnDocNodes.parent_node_id.is_(parent_node_id) if parent_node_id is None else HasnDocNodes.parent_node_id == parent_node_id,
                        HasnDocNodes.status == 'active',
                    )
                )
            ).scalars().first()
            if existing:
                parent_node_id = existing.node_id
            else:
                created = await DocService.create_node(db, space_id=s.space_id, actor_hasn_id=actor_hasn_id, node_type='directory', title=name, parent_node_id=parent_node_id)
                parent_node_id = created['node_id']
        leaf = await DocService.create_node(
            db, space_id=s.space_id, actor_hasn_id=actor_hasn_id, node_type='article', title=article_title, parent_node_id=parent_node_id,
            article_id=article_id, visibility=placement.get('node_visibility') if allow_visibility else None,
            password=placement.get('node_password') if allow_visibility else None, allow_visibility=allow_visibility,
        )
        return {'space_id': s.space_id, 'node_id': leaf['node_id'], 'parent_node_id': parent_node_id}


doc_service = DocService()
