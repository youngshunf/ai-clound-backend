"""圈子体系服务（设计文档 16）。

圈子 = 有边界/成员/治理的子社区。圈主必为 Human；Agent 可作成员（带主人、透明）。
内容归属由 circle_id 表达，圈内内容流按圈子可见性 + 成员关系裁剪。
"""

from __future__ import annotations

import re

from typing import TYPE_CHECKING, Any

from sqlalchemy import func, select, update

from backend.app.hasn_community.model import HasnArticles, HasnCircleMembers, HasnCircles, HasnPosts
from backend.app.hasn_community.service.community_cards import fetch_article_cards, fetch_post_cards
from backend.app.hasn_im.application.provider import get_presence_query
from backend.common.exception import errors
from backend.database.db import uuid4_str
from backend.utils.timezone import timezone

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

_SLUG_KEEP = re.compile(r'[^a-z0-9]+')
_VALID_JOIN = {'open', 'approval', 'invite'}
_VALID_POST = {'members', 'approval', 'owner_admin'}
_VALID_VIS = {'public', 'private'}
_MGMT_ROLES = {'owner', 'admin'}

_presence_query = get_presence_query()


def _slugify(name: str) -> str:
    base = _SLUG_KEEP.sub('-', (name or '').lower()).strip('-')
    return base[:72] if base else f'cir-{uuid4_str()[:8]}'


class CircleService:
    @staticmethod
    async def _get(db: AsyncSession, ident: str) -> HasnCircles | None:
        col = HasnCircles.circle_id if ident.startswith('cir_') else HasnCircles.slug
        return (await db.execute(select(HasnCircles).where(col == ident))).scalars().first()

    @staticmethod
    async def _membership(db: AsyncSession, circle_id: str, hasn_id: str) -> HasnCircleMembers | None:
        return (
            await db.execute(
                select(HasnCircleMembers).where(HasnCircleMembers.circle_id == circle_id, HasnCircleMembers.member_hasn_id == hasn_id)
            )
        ).scalars().first()

    @staticmethod
    def _circle_dict(c: HasnCircles, *, my_role: str | None = None, my_status: str | None = None) -> dict[str, Any]:
        return {
            'circle_id': c.circle_id, 'name': c.name, 'slug': c.slug, 'description': c.description,
            'cover_url': c.cover_url, 'avatar_url': c.avatar_url, 'owner_hasn_id': c.owner_hasn_id,
            'visibility': c.visibility, 'join_policy': c.join_policy, 'post_policy': c.post_policy,
            'member_count': c.member_count, 'content_count': c.content_count, 'status': c.status,
            'my_role': my_role, 'my_status': my_status,
        }

    # ---------- CRUD ----------

    @staticmethod
    async def create_circle(
        db: AsyncSession, *, owner_hasn_id: str, owner_user_id: int, name: str,
        description: str | None = None, cover_url: str | None = None, avatar_url: str | None = None,
        visibility: str = 'public', join_policy: str = 'approval', post_policy: str = 'members',
        workspace_kind: str = 'personal', workspace_id: str | None = None,
    ) -> dict[str, Any]:
        name = (name or '').strip()
        if not name:
            raise errors.RequestError(msg='圈子名不能为空')
        if visibility not in _VALID_VIS or join_policy not in _VALID_JOIN or post_policy not in _VALID_POST:
            raise errors.RequestError(msg='圈子策略取值非法')
        circle_id = f'cir_{uuid4_str()[:12]}'
        slug = _slugify(name)
        if (await db.execute(select(HasnCircles.id).where(HasnCircles.slug == slug))).first():
            slug = f'cir-{uuid4_str()[:8]}'
        circle = HasnCircles(
            circle_id=circle_id, name=name, slug=slug, description=description, cover_url=cover_url, avatar_url=avatar_url,
            owner_hasn_id=owner_hasn_id, origin_workspace_kind=workspace_kind, origin_workspace_id=workspace_id or str(owner_user_id),
            visibility=visibility, join_policy=join_policy, post_policy=post_policy, member_count=1, content_count=0, status='active',
        )
        db.add(circle)
        # 建者自动 owner 成员（圈主必为 Human）
        db.add(HasnCircleMembers(
            circle_id=circle_id, member_hasn_id=owner_hasn_id, member_type='human', owner_hasn_id=owner_hasn_id,
            role='owner', status='active', joined_time=timezone.now(),
        ))
        await db.flush()
        return CircleService._circle_dict(circle, my_role='owner', my_status='active')

    @staticmethod
    async def get_circle(db: AsyncSession, ident: str, *, viewer_hasn_id: str | None = None, public_only: bool = False) -> dict[str, Any]:
        c = await CircleService._get(db, ident)
        if not c or c.status == 'blocked':
            raise errors.NotFoundError(msg='圈子不存在')
        if public_only and c.visibility != 'public':
            raise errors.NotFoundError(msg='圈子不存在')
        my_role = my_status = None
        if viewer_hasn_id:
            m = await CircleService._membership(db, c.circle_id, viewer_hasn_id)
            if m:
                my_role, my_status = m.role, m.status
        return CircleService._circle_dict(c, my_role=my_role, my_status=my_status)

    @staticmethod
    async def update_circle(db: AsyncSession, *, ident: str, actor_hasn_id: str, **fields: Any) -> dict[str, Any]:
        c = await CircleService._get(db, ident)
        if not c:
            raise errors.NotFoundError(msg='圈子不存在')
        m = await CircleService._membership(db, c.circle_id, actor_hasn_id)
        if not m or m.role not in _MGMT_ROLES or m.status != 'active':
            raise errors.ForbiddenError(msg='仅圈主/管理员可改圈子')
        for k in ('name', 'description', 'cover_url', 'avatar_url'):
            if fields.get(k) is not None:
                setattr(c, k, fields[k])
        for k, valid in (('visibility', _VALID_VIS), ('join_policy', _VALID_JOIN), ('post_policy', _VALID_POST)):
            if fields.get(k) is not None:
                if fields[k] not in valid:
                    raise errors.RequestError(msg=f'{k} 取值非法')
                setattr(c, k, fields[k])
        await db.flush()
        return CircleService._circle_dict(c, my_role=m.role, my_status=m.status)

    # ---------- 成员生命周期 ----------

    @staticmethod
    async def join_circle(db: AsyncSession, *, ident: str, member_hasn_id: str, member_type: str, owner_hasn_id: str) -> dict[str, Any]:
        c = await CircleService._get(db, ident)
        if not c or c.status != 'active':
            raise errors.NotFoundError(msg='圈子不存在')
        existing = await CircleService._membership(db, c.circle_id, member_hasn_id)
        if existing and existing.status in ('active', 'pending'):
            return {'circle_id': c.circle_id, 'status': existing.status, 'role': existing.role}
        if existing and existing.status == 'banned':
            raise errors.ForbiddenError(msg='你已被该圈封禁')
        if c.join_policy == 'invite':
            raise errors.ForbiddenError(msg='该圈仅限邀请加入')
        new_status = 'active' if c.join_policy == 'open' else 'pending'
        if existing:  # left -> rejoin
            existing.status = new_status
            existing.role = 'member'
            existing.joined_time = timezone.now() if new_status == 'active' else None
        else:
            db.add(HasnCircleMembers(
                circle_id=c.circle_id, member_hasn_id=member_hasn_id, member_type=member_type, owner_hasn_id=owner_hasn_id,
                role='member', status=new_status, joined_time=timezone.now() if new_status == 'active' else None,
            ))
        if new_status == 'active':
            await db.execute(update(HasnCircles).where(HasnCircles.circle_id == c.circle_id).values(member_count=HasnCircles.member_count + 1))
        await db.flush()
        return {'circle_id': c.circle_id, 'status': new_status, 'role': 'member'}

    @staticmethod
    async def leave_circle(db: AsyncSession, *, ident: str, member_hasn_id: str) -> dict[str, Any]:
        c = await CircleService._get(db, ident)
        if not c:
            raise errors.NotFoundError(msg='圈子不存在')
        m = await CircleService._membership(db, c.circle_id, member_hasn_id)
        if not m or m.status == 'left':
            return {'circle_id': c.circle_id, 'status': 'left'}
        if m.role == 'owner':
            raise errors.ForbiddenError(msg='圈主不能直接退出，请先转让或解散')
        was_active = m.status == 'active'
        m.status = 'left'
        if was_active:
            await db.execute(update(HasnCircles).where(HasnCircles.circle_id == c.circle_id).values(member_count=func.greatest(HasnCircles.member_count - 1, 0)))
        await db.flush()
        return {'circle_id': c.circle_id, 'status': 'left'}

    @staticmethod
    async def _assert_manager(db: AsyncSession, circle_id: str, actor_hasn_id: str) -> HasnCircleMembers:
        m = await CircleService._membership(db, circle_id, actor_hasn_id)
        if not m or m.role not in _MGMT_ROLES or m.status != 'active':
            raise errors.ForbiddenError(msg='仅圈主/管理员可治理成员')
        return m

    @staticmethod
    async def moderate_member(db: AsyncSession, *, ident: str, target_hasn_id: str, actor_hasn_id: str, action: str, role: str | None = None) -> dict[str, Any]:
        c = await CircleService._get(db, ident)
        if not c:
            raise errors.NotFoundError(msg='圈子不存在')
        await CircleService._assert_manager(db, c.circle_id, actor_hasn_id)
        m = await CircleService._membership(db, c.circle_id, target_hasn_id)
        if not m:
            raise errors.NotFoundError(msg='成员不存在')
        if m.role == 'owner':
            raise errors.ForbiddenError(msg='不能对圈主执行该操作')
        if action == 'approve':
            if m.status != 'pending':
                raise errors.RequestError(msg='该成员非待审批状态')
            m.status = 'active'
            m.joined_time = timezone.now()
            await db.execute(update(HasnCircles).where(HasnCircles.circle_id == c.circle_id).values(member_count=HasnCircles.member_count + 1))
        elif action == 'reject':
            m.status = 'left'
        elif action == 'ban':
            was_active = m.status == 'active'
            m.status = 'banned'
            if was_active:
                await db.execute(update(HasnCircles).where(HasnCircles.circle_id == c.circle_id).values(member_count=func.greatest(HasnCircles.member_count - 1, 0)))
        elif action == 'set-role':
            if role not in ('admin', 'member'):
                raise errors.RequestError(msg='role 仅支持 admin/member')
            m.role = role
        else:
            raise errors.RequestError(msg='未知治理动作')
        await db.flush()
        return {'circle_id': c.circle_id, 'member_hasn_id': target_hasn_id, 'role': m.role, 'status': m.status}

    @staticmethod
    async def invite(db: AsyncSession, *, ident: str, actor_hasn_id: str, invitee_hasn_id: str, invitee_type: str = 'human', invitee_owner_hasn_id: str | None = None) -> dict[str, Any]:
        c = await CircleService._get(db, ident)
        if not c:
            raise errors.NotFoundError(msg='圈子不存在')
        await CircleService._assert_manager(db, c.circle_id, actor_hasn_id)
        existing = await CircleService._membership(db, c.circle_id, invitee_hasn_id)
        if existing and existing.status in ('active', 'pending'):
            return {'circle_id': c.circle_id, 'member_hasn_id': invitee_hasn_id, 'status': existing.status}
        if existing:
            existing.status = 'active'
            existing.role = 'member'
            existing.invited_by_hasn_id = actor_hasn_id
            existing.joined_time = timezone.now()
        else:
            db.add(HasnCircleMembers(
                circle_id=c.circle_id, member_hasn_id=invitee_hasn_id, member_type=invitee_type,
                owner_hasn_id=invitee_owner_hasn_id or invitee_hasn_id, role='member', status='active',
                invited_by_hasn_id=actor_hasn_id, joined_time=timezone.now(),
            ))
        await db.execute(update(HasnCircles).where(HasnCircles.circle_id == c.circle_id).values(member_count=HasnCircles.member_count + 1))
        await db.flush()
        return {'circle_id': c.circle_id, 'member_hasn_id': invitee_hasn_id, 'status': 'active'}

    @staticmethod
    async def _enrich_members(db: AsyncSession, members: list[dict[str, Any]]) -> None:
        """给一批已序列化的圈子成员 dict 就地补「昵称/头像 + 专家名称 + 主人 + 实时在线态」。

        圈子成员 dict 只存 member_hasn_id/member_type/owner_hasn_id，原样下发会让 WebUI
        露出裸 HASN ID。这里批量回填身份信息，让前端统一走公共 AgentIdentity 组件渲染：

        - human 成员：nickname/avatar（HasnHumans 权威）。
        - agent 成员：display_name/avatar/profession（HasnAgents 权威，全网分身都有头衔）
          + owner_display_name（主人昵称，HasnHumans）+ online_status（Redis presence，断线即
          offline，不读持久列避免僵尸在线）。

        诚实留空：查不到 profession → ''；display_name 兜底用 member_hasn_id（前端再兜底）。
        """
        from backend.app.hasn_core import HasnAgents, HasnHumans

        human_ids = {m['member_hasn_id'] for m in members if m.get('member_type') == 'human'}
        agent_ids = {m['member_hasn_id'] for m in members if m.get('member_type') == 'agent'}
        owner_ids = {m['owner_hasn_id'] for m in members if m.get('member_type') == 'agent' and m.get('owner_hasn_id')}

        human_map: dict[str, Any] = {}
        if human_ids or owner_ids:
            rows = (
                await db.execute(
                    select(HasnHumans.hasn_id, HasnHumans.nickname, HasnHumans.avatar).where(
                        HasnHumans.hasn_id.in_(human_ids | owner_ids)
                    )
                )
            ).all()
            human_map = {r.hasn_id: r for r in rows}

        agent_map: dict[str, Any] = {}
        if agent_ids:
            rows = (
                await db.execute(
                    select(
                        HasnAgents.hasn_id,
                        HasnAgents.display_name,
                        HasnAgents.avatar,
                        HasnAgents.profession,
                    ).where(HasnAgents.hasn_id.in_(agent_ids))
                )
            ).all()
            agent_map = {r.hasn_id: r for r in rows}
        online_map = await _presence_query.get_online_map(list(agent_ids)) if agent_ids else {}

        for m in members:
            hid = m['member_hasn_id']
            if m.get('member_type') == 'agent':
                a = agent_map.get(hid)
                m['display_name'] = (a.display_name if a else None) or hid
                m['avatar'] = a.avatar if a else None
                m['profession'] = (a.profession or '') if a else ''
                owner = human_map.get(m.get('owner_hasn_id'))
                m['owner_display_name'] = owner.nickname if owner else None
                m['online_status'] = 'online' if online_map.get(hid) else 'offline'
            else:
                h = human_map.get(hid)
                m['display_name'] = (h.nickname if h else None) or hid
                m['avatar'] = h.avatar if h else None

    @staticmethod
    async def list_members(db: AsyncSession, *, ident: str, status: str = 'active', limit: int = 50) -> list[dict[str, Any]]:
        c = await CircleService._get(db, ident)
        if not c:
            raise errors.NotFoundError(msg='圈子不存在')
        stmt = select(HasnCircleMembers).where(HasnCircleMembers.circle_id == c.circle_id)
        if status != 'all':
            stmt = stmt.where(HasnCircleMembers.status == status)
        stmt = stmt.order_by(HasnCircleMembers.role, HasnCircleMembers.created_time).limit(limit)
        members = [
            {'member_hasn_id': m.member_hasn_id, 'member_type': m.member_type, 'role': m.role, 'status': m.status,
             'owner_hasn_id': m.owner_hasn_id, 'joined_time': m.joined_time.isoformat() if m.joined_time else None}
            for m in (await db.execute(stmt)).scalars().all()
        ]
        await CircleService._enrich_members(db, members)
        return members

    @staticmethod
    async def list_mine(db: AsyncSession, *, member_hasn_id: str) -> list[dict[str, Any]]:
        stmt = (
            select(HasnCircles, HasnCircleMembers.role, HasnCircleMembers.status)
            .join(HasnCircleMembers, HasnCircleMembers.circle_id == HasnCircles.circle_id)
            .where(HasnCircleMembers.member_hasn_id == member_hasn_id, HasnCircleMembers.status.in_(['active', 'pending']), HasnCircles.status == 'active')
            .order_by(HasnCircleMembers.created_time.desc())
        )
        return [CircleService._circle_dict(row.HasnCircles, my_role=row.role, my_status=row.status) for row in (await db.execute(stmt)).all()]

    @staticmethod
    async def discover(db: AsyncSession, *, cursor: str | None = None, limit: int = 20) -> dict[str, Any]:
        stmt = select(HasnCircles).where(HasnCircles.visibility == 'public', HasnCircles.status == 'active')
        cur = int(cursor) if cursor and cursor.isdigit() else None
        if cur is not None:
            stmt = stmt.where(HasnCircles.id < cur)
        stmt = stmt.order_by(HasnCircles.id.desc()).limit(limit + 1)
        rows = (await db.execute(stmt)).scalars().all()
        has_more = len(rows) > limit
        rows = rows[:limit]
        return {'items': [CircleService._circle_dict(c) for c in rows], 'next_cursor': str(rows[-1].id) if has_more and rows else None}

    # ---------- 圈子内容流 ----------

    @staticmethod
    async def get_circle_feed(db: AsyncSession, ident: str, *, cursor: str | None = None, limit: int = 20, viewer_hasn_id: str | None = None, public_only: bool = False) -> dict[str, Any]:
        c = await CircleService._get(db, ident)
        if not c or c.status == 'blocked':
            raise errors.NotFoundError(msg='圈子不存在')
        if c.visibility == 'private':
            m = await CircleService._membership(db, c.circle_id, viewer_hasn_id) if viewer_hasn_id else None
            if public_only or not m or m.status != 'active':
                raise errors.ForbiddenError(msg='私密圈内容仅成员可见')
        # 圈子流按 published_time 倒序，游标用 published_time ISO 字符串
        post_q = select(HasnPosts.id.label('rid'), HasnPosts.post_id.label('cid')).where(HasnPosts.circle_id == c.circle_id, HasnPosts.status == 'published')
        art_q = select(HasnArticles.id.label('rid'), HasnArticles.article_id.label('cid')).where(HasnArticles.circle_id == c.circle_id, HasnArticles.status == 'published')
        post_rows = (await db.execute(post_q.order_by(HasnPosts.published_time.desc()).limit(200))).all()
        art_rows = (await db.execute(art_q.order_by(HasnArticles.published_time.desc()).limit(200))).all()
        post_cards = await fetch_post_cards(db, [r.cid for r in post_rows])
        art_cards = await fetch_article_cards(db, [r.cid for r in art_rows])
        merged = [post_cards[r.cid] for r in post_rows if r.cid in post_cards] + [art_cards[r.cid] for r in art_rows if r.cid in art_cards]
        merged.sort(key=lambda c2: (c2.get('published_time') or ''), reverse=True)
        # 简单游标：基于 published_time 字符串
        if cursor and not cursor.isdigit():
            merged = [m for m in merged if (m.get('published_time') or '') < cursor]
        has_more = len(merged) > limit
        page = merged[:limit]
        next_cursor = page[-1]['published_time'] if has_more and page else None
        return {'circle': CircleService._circle_dict(c), 'items': page, 'next_cursor': next_cursor}

    @staticmethod
    async def moderate_content(db: AsyncSession, *, ident: str, content_type: str, content_id: str, actor_hasn_id: str, action: str) -> dict[str, Any]:
        c = await CircleService._get(db, ident)
        if not c:
            raise errors.NotFoundError(msg='圈子不存在')
        await CircleService._assert_manager(db, c.circle_id, actor_hasn_id)
        model = HasnPosts if content_type == 'post' else HasnArticles
        id_col = HasnPosts.post_id if content_type == 'post' else HasnArticles.article_id
        obj = (await db.execute(select(model).where(id_col == content_id, model.circle_id == c.circle_id))).scalars().first()
        if not obj:
            raise errors.NotFoundError(msg='圈内内容不存在')
        if action == 'approve':
            obj.status = 'published'
            if obj.published_time is None:
                obj.published_time = timezone.now()
        elif action in ('hide', 'reject'):
            obj.status = 'hidden'
        elif action == 'delete':
            obj.status = 'deleted'
        else:
            raise errors.RequestError(msg='未知内容治理动作')
        await db.flush()
        return {'circle_id': c.circle_id, 'content_id': content_id, 'status': obj.status}

    # ---------- 发布闸门（供发布汇聚调用） ----------

    @staticmethod
    async def assert_can_post(db: AsyncSession, *, circle_id: str, actor_hasn_id: str) -> tuple[HasnCircles, bool]:
        """校验 actor 是圈 active 成员且满足 post_policy；返回 (circle, needs_review)。"""
        c = await CircleService._get(db, circle_id)
        if not c or c.status != 'active':
            raise errors.NotFoundError(msg='圈子不存在')
        m = await CircleService._membership(db, c.circle_id, actor_hasn_id)
        if not m or m.status != 'active':
            raise errors.ForbiddenError(msg='仅圈内 active 成员可在圈内发布')
        if c.post_policy == 'owner_admin' and m.role not in _MGMT_ROLES:
            raise errors.ForbiddenError(msg='该圈仅管理者可发布')
        needs_review = c.post_policy == 'approval' and m.role not in _MGMT_ROLES
        return c, needs_review

    @staticmethod
    async def bump_content_count(db: AsyncSession, *, circle_id: str) -> None:
        await db.execute(update(HasnCircles).where(HasnCircles.circle_id == circle_id).values(content_count=HasnCircles.content_count + 1))


circle_service = CircleService()
