"""圈子体系服务（设计文档 16）。

圈子 = 有边界/成员/治理的子社区。圈主必为 Human；Agent 可作成员（带主人、透明）。
内容归属由 circle_id 表达，圈内内容流按圈子可见性 + 成员关系裁剪。
"""

from __future__ import annotations

import base64
import json
import re

from datetime import datetime, timedelta
from typing import TYPE_CHECKING, Any

from sqlalchemy import and_, case, func, literal, or_, select, union_all, update

from backend.app.hasn_community.model import HasnArticles, HasnCircleMembers, HasnCircles, HasnPosts
from backend.app.hasn_community.service.community_cards import fetch_article_cards, fetch_post_cards
from backend.app.hasn_community.service.content_visibility import content_visibility_sql
from backend.app.hasn_community.service.notification_service import notification_service
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


def _encode_cursor(payload: dict[str, Any]) -> str:
    raw = json.dumps(payload, ensure_ascii=False, separators=(',', ':')).encode()
    return base64.urlsafe_b64encode(raw).decode().rstrip('=')


def _decode_cursor(cursor: str | None) -> dict[str, Any] | None:
    if not cursor:
        return None
    try:
        padded = cursor + '=' * (-len(cursor) % 4)
        payload = json.loads(base64.urlsafe_b64decode(padded).decode())
    except (ValueError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise errors.RequestError(msg='分页游标无效') from exc
    if not isinstance(payload, dict):
        raise errors.RequestError(msg='分页游标无效')
    return payload


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
            'created_time': c.created_time.isoformat() if c.created_time else None,
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
        item = CircleService._circle_dict(c, my_role=my_role, my_status=my_status)
        await CircleService._enrich_discovery(
            db,
            items=[item],
            viewer_hasn_id=viewer_hasn_id,
        )
        return item

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
    async def _notify_managers(
        db: AsyncSession,
        *,
        circle: HasnCircles,
        actor_hasn_id: str,
        ntype: str,
        title: str,
        content_type: str | None = None,
        content_id: str | None = None,
    ) -> None:
        """把待处理事项通知给所有有效圈主和管理员。"""
        manager_ids = (
            await db.execute(
                select(HasnCircleMembers.member_hasn_id).where(
                    HasnCircleMembers.circle_id == circle.circle_id,
                    HasnCircleMembers.role.in_(_MGMT_ROLES),
                    HasnCircleMembers.status == 'active',
                )
            )
        ).scalars().all()
        for manager_hasn_id in manager_ids:
            await notification_service.notify_circle_event(
                db,
                recipient_hasn_id=manager_hasn_id,
                actor_hasn_id=actor_hasn_id,
                ntype=ntype,
                title=title,
                circle_id=circle.circle_id,
                circle_name=circle.name,
                content_type=content_type,
                content_id=content_id,
            )

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
        if new_status == 'pending':
            await CircleService._notify_managers(
                db,
                circle=c,
                actor_hasn_id=member_hasn_id,
                ntype='circle_join_pending',
                title=f'有人申请加入「{c.name}」',
            )
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
        event_type = {
            'approve': 'circle_join_approved',
            'reject': 'circle_join_rejected',
            'ban': 'circle_member_banned',
            'set-role': 'circle_role_changed',
        }[action]
        title = {
            'approve': f'你加入「{c.name}」的申请已通过',
            'reject': f'你加入「{c.name}」的申请未通过',
            'ban': f'你在「{c.name}」的成员资格已被停用',
            'set-role': f'你在「{c.name}」的角色已更新',
        }[action]
        await notification_service.notify_circle_event(
            db,
            recipient_hasn_id=target_hasn_id,
            actor_hasn_id=actor_hasn_id,
            ntype=event_type,
            title=title,
            circle_id=c.circle_id,
            circle_name=c.name,
            recipient_type=m.member_type,
            recipient_owner_hasn_id=m.owner_hasn_id,
        )
        return {'circle_id': c.circle_id, 'member_hasn_id': target_hasn_id, 'role': m.role, 'status': m.status}

    @staticmethod
    async def invite(db: AsyncSession, *, ident: str, actor_hasn_id: str, invitee_hasn_id: str, invitee_type: str = 'human', invitee_owner_hasn_id: str | None = None) -> dict[str, Any]:
        from backend.app.hasn_core import identity

        c = await CircleService._get(db, ident)
        if not c:
            raise errors.NotFoundError(msg='圈子不存在')
        await CircleService._assert_manager(db, c.circle_id, actor_hasn_id)
        if invitee_type == 'agent':
            invitee_agent = await identity.get_agent(db, hasn_id=invitee_hasn_id)
            authoritative_owner = invitee_agent.owner_id if invitee_agent else None
            if not authoritative_owner:
                raise errors.NotFoundError(msg='受邀分身不存在')
        elif invitee_type == 'human':
            human_exists = await identity.get_human(db, hasn_id=invitee_hasn_id)
            if not human_exists:
                raise errors.NotFoundError(msg='受邀用户不存在')
            authoritative_owner = invitee_hasn_id
        else:
            raise errors.RequestError(msg='受邀成员类型非法')
        if invitee_owner_hasn_id and invitee_owner_hasn_id != authoritative_owner:
            raise errors.RequestError(msg='受邀分身主人信息与权威身份不一致')
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
                owner_hasn_id=authoritative_owner, role='member', status='active',
                invited_by_hasn_id=actor_hasn_id, joined_time=timezone.now(),
            ))
        await db.execute(update(HasnCircles).where(HasnCircles.circle_id == c.circle_id).values(member_count=HasnCircles.member_count + 1))
        await db.flush()
        await notification_service.notify_circle_event(
            db,
            recipient_hasn_id=invitee_hasn_id,
            actor_hasn_id=actor_hasn_id,
            ntype='circle_invited',
            title=f'你已受邀加入「{c.name}」',
            circle_id=c.circle_id,
            circle_name=c.name,
            recipient_type=invitee_type,
            recipient_owner_hasn_id=authoritative_owner,
        )
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
        from backend.app.hasn_core import identity

        human_ids = {m['member_hasn_id'] for m in members if m.get('member_type') == 'human'}
        agent_ids = {m['member_hasn_id'] for m in members if m.get('member_type') == 'agent'}
        owner_ids = {m['owner_hasn_id'] for m in members if m.get('member_type') == 'agent' and m.get('owner_hasn_id')}

        human_map = await identity.refs_for_humans(db, hasn_ids=list(human_ids | owner_ids))
        agent_map = await identity.refs_for_agents(db, hasn_ids=list(agent_ids))
        online_map = await _presence_query.get_online_map(list(agent_ids)) if agent_ids else {}

        for m in members:
            hid = m['member_hasn_id']
            if m.get('member_type') == 'agent':
                a = agent_map.get(hid)
                m['display_name'] = (a.display_name if a else None) or hid
                m['avatar'] = a.avatar if a else None
                m['profession'] = (a.profession or '') if a else ''
                owner_hasn_id = m.get('owner_hasn_id')
                owner = human_map.get(owner_hasn_id) if isinstance(owner_hasn_id, str) else None
                m['owner_display_name'] = owner.nickname if owner else None
                m['online_status'] = 'online' if online_map.get(hid) else 'offline'
            else:
                h = human_map.get(hid)
                m['display_name'] = (h.nickname if h else None) or hid
                m['avatar'] = h.avatar if h else None

    @staticmethod
    async def list_members(
        db: AsyncSession,
        *,
        ident: str,
        status: str = 'active',
        cursor: str | None = None,
        limit: int = 50,
    ) -> dict[str, Any]:
        c = await CircleService._get(db, ident)
        if not c:
            raise errors.NotFoundError(msg='圈子不存在')
        if status not in {'active', 'pending', 'banned', 'left', 'all'}:
            raise errors.RequestError(msg='成员状态筛选值非法')
        role_order = case(
            (HasnCircleMembers.role == 'owner', 0),
            (HasnCircleMembers.role == 'admin', 1),
            else_=2,
        )
        stmt = select(HasnCircleMembers, role_order.label('role_order')).where(
            HasnCircleMembers.circle_id == c.circle_id
        )
        if status != 'all':
            stmt = stmt.where(HasnCircleMembers.status == status)
        cursor_data = _decode_cursor(cursor)
        if cursor_data:
            try:
                cursor_role = int(cursor_data['role'])
                cursor_id = int(cursor_data['id'])
            except (KeyError, TypeError, ValueError) as exc:
                raise errors.RequestError(msg='成员分页游标无效') from exc
            stmt = stmt.where(
                or_(
                    role_order > cursor_role,
                    and_(role_order == cursor_role, HasnCircleMembers.id > cursor_id),
                )
            )
        rows = (
            await db.execute(
                stmt.order_by(role_order.asc(), HasnCircleMembers.id.asc()).limit(limit + 1)
            )
        ).all()
        has_more = len(rows) > limit
        rows = rows[:limit]
        members = [
            {'member_hasn_id': m.member_hasn_id, 'member_type': m.member_type, 'role': m.role, 'status': m.status,
             'owner_hasn_id': m.owner_hasn_id, 'joined_time': m.joined_time.isoformat() if m.joined_time else None}
            for m, _role_order in rows
        ]
        await CircleService._enrich_members(db, members)
        next_cursor = None
        if has_more and rows:
            last_member, last_role_order = rows[-1]
            next_cursor = _encode_cursor({'role': last_role_order, 'id': last_member.id})
        return {'items': members, 'next_cursor': next_cursor}

    @staticmethod
    async def list_mine(
        db: AsyncSession,
        *,
        member_hasn_id: str,
        cursor: str | None = None,
        limit: int = 20,
    ) -> dict[str, Any]:
        stmt = (
            select(
                HasnCircles,
                HasnCircleMembers.id.label('membership_id'),
                HasnCircleMembers.created_time.label('membership_created_time'),
                HasnCircleMembers.role,
                HasnCircleMembers.status,
            )
            .join(HasnCircleMembers, HasnCircleMembers.circle_id == HasnCircles.circle_id)
            .where(HasnCircleMembers.member_hasn_id == member_hasn_id, HasnCircleMembers.status.in_(['active', 'pending']), HasnCircles.status == 'active')
        )
        cursor_data = _decode_cursor(cursor)
        if cursor_data:
            try:
                cursor_time = datetime.fromisoformat(str(cursor_data['time']))
                cursor_id = int(cursor_data['id'])
            except (KeyError, TypeError, ValueError) as exc:
                raise errors.RequestError(msg='我的圈子分页游标无效') from exc
            stmt = stmt.where(
                or_(
                    HasnCircleMembers.created_time < cursor_time,
                    and_(
                        HasnCircleMembers.created_time == cursor_time,
                        HasnCircleMembers.id < cursor_id,
                    ),
                )
            )
        rows = (
            await db.execute(
                stmt.order_by(
                    HasnCircleMembers.created_time.desc(),
                    HasnCircleMembers.id.desc(),
                ).limit(limit + 1)
            )
        ).all()
        has_more = len(rows) > limit
        rows = rows[:limit]
        items = [
            CircleService._circle_dict(row.HasnCircles, my_role=row.role, my_status=row.status)
            for row in rows
        ]
        next_cursor = None
        if has_more and rows:
            last = rows[-1]
            next_cursor = _encode_cursor(
                {
                    'time': last.membership_created_time.isoformat(),
                    'id': last.membership_id,
                }
            )
        return {'items': items, 'next_cursor': next_cursor}

    @staticmethod
    async def _enrich_discovery(
        db: AsyncSession,
        *,
        items: list[dict[str, Any]],
        viewer_hasn_id: str | None,
    ) -> None:
        """批量补发现页关系态、管理待审数、七日活跃与代表成员。"""
        circle_ids = [item['circle_id'] for item in items]
        if not circle_ids:
            return
        membership_map: dict[str, HasnCircleMembers] = {}
        if viewer_hasn_id:
            memberships = (
                await db.execute(
                    select(HasnCircleMembers).where(
                        HasnCircleMembers.circle_id.in_(circle_ids),
                        HasnCircleMembers.member_hasn_id == viewer_hasn_id,
                    )
                )
            ).scalars().all()
            membership_map = {member.circle_id: member for member in memberships}

        manager_circle_ids = {
            circle_id
            for circle_id, member in membership_map.items()
            if member.role in _MGMT_ROLES and member.status == 'active'
        }
        pending_map: dict[str, int] = {}
        if manager_circle_ids:
            pending_rows = (
                await db.execute(
                    select(
                        HasnCircleMembers.circle_id,
                        func.count(HasnCircleMembers.id).label('pending_count'),
                    )
                    .where(
                        HasnCircleMembers.circle_id.in_(manager_circle_ids),
                        HasnCircleMembers.status == 'pending',
                    )
                    .group_by(HasnCircleMembers.circle_id)
                )
            ).all()
            pending_map = {row.circle_id: int(row.pending_count) for row in pending_rows}

        today = timezone.now().date()
        start_time = timezone.now() - timedelta(days=7)
        content_stream = union_all(
            select(
                HasnPosts.circle_id.label('circle_id'),
                HasnPosts.published_time.label('published_time'),
            ).where(
                HasnPosts.circle_id.in_(circle_ids),
                HasnPosts.status == 'published',
                HasnPosts.published_time.is_not(None),
            ),
            select(
                HasnArticles.circle_id.label('circle_id'),
                HasnArticles.published_time.label('published_time'),
            ).where(
                HasnArticles.circle_id.in_(circle_ids),
                HasnArticles.status == 'published',
                HasnArticles.published_time.is_not(None),
            ),
        ).subquery()
        activity_rows = (
            await db.execute(
                select(
                    content_stream.c.circle_id,
                    func.date(content_stream.c.published_time).label('activity_date'),
                    func.count().label('activity_count'),
                )
                .where(content_stream.c.published_time >= start_time)
                .group_by(
                    content_stream.c.circle_id,
                    func.date(content_stream.c.published_time),
                )
            )
        ).all()
        activity_map = {
            (row.circle_id, row.activity_date): int(row.activity_count)
            for row in activity_rows
        }

        role_order = case(
            (HasnCircleMembers.role == 'owner', 0),
            (HasnCircleMembers.role == 'admin', 1),
            else_=2,
        )
        ranked_members = (
            select(
                HasnCircleMembers.circle_id.label('circle_id'),
                HasnCircleMembers.member_hasn_id.label('member_hasn_id'),
                HasnCircleMembers.member_type.label('member_type'),
                HasnCircleMembers.owner_hasn_id.label('owner_hasn_id'),
                HasnCircleMembers.role.label('role'),
                HasnCircleMembers.status.label('status'),
                HasnCircleMembers.joined_time.label('joined_time'),
                func.row_number()
                .over(
                    partition_by=HasnCircleMembers.circle_id,
                    order_by=(role_order.asc(), HasnCircleMembers.id.asc()),
                )
                .label('member_rank'),
            )
            .where(
                HasnCircleMembers.circle_id.in_(circle_ids),
                HasnCircleMembers.status == 'active',
            )
            .subquery()
        )
        top_rows = (
            await db.execute(
                select(ranked_members)
                .where(ranked_members.c.member_rank <= 3)
                .order_by(ranked_members.c.circle_id, ranked_members.c.member_rank)
            )
        ).mappings().all()
        top_members = [
            {
                'circle_id': row['circle_id'],
                'member_hasn_id': row['member_hasn_id'],
                'member_type': row['member_type'],
                'owner_hasn_id': row['owner_hasn_id'],
                'role': row['role'],
                'status': row['status'],
                'joined_time': row['joined_time'].isoformat() if row['joined_time'] else None,
            }
            for row in top_rows
        ]
        await CircleService._enrich_members(db, top_members)
        top_member_map: dict[str, list[dict[str, Any]]] = {}
        for member in top_members:
            top_member_map.setdefault(member.pop('circle_id'), []).append(member)

        for item in items:
            circle_id = item['circle_id']
            membership = membership_map.get(circle_id)
            item['my_role'] = membership.role if membership else None
            item['my_status'] = membership.status if membership else None
            item['pending_count'] = (
                pending_map.get(circle_id, 0) if circle_id in manager_circle_ids else None
            )
            item['activity_7d'] = [
                {
                    'date': (today - timedelta(days=offset)).isoformat(),
                    'count': activity_map.get(
                        (circle_id, today - timedelta(days=offset)),
                        0,
                    ),
                }
                for offset in range(6, -1, -1)
            ]
            item['top_members'] = top_member_map.get(circle_id, [])

    @staticmethod
    async def discover(
        db: AsyncSession,
        *,
        viewer_hasn_id: str | None = None,
        sort: str = 'active',
        join_policy: str | None = None,
        cursor: str | None = None,
        limit: int = 20,
    ) -> dict[str, Any]:
        if sort not in {'active', 'newest', 'members'}:
            raise errors.RequestError(msg='圈子排序值非法')
        if join_policy is not None and join_policy not in _VALID_JOIN:
            raise errors.RequestError(msg='加入策略筛选值非法')
        content_stream = union_all(
            select(
                HasnPosts.circle_id.label('circle_id'),
                HasnPosts.published_time.label('published_time'),
            ).where(
                HasnPosts.status == 'published',
                HasnPosts.circle_id.is_not(None),
                HasnPosts.published_time.is_not(None),
            ),
            select(
                HasnArticles.circle_id.label('circle_id'),
                HasnArticles.published_time.label('published_time'),
            ).where(
                HasnArticles.status == 'published',
                HasnArticles.circle_id.is_not(None),
                HasnArticles.published_time.is_not(None),
            ),
        ).subquery()
        since = timezone.now() - timedelta(days=7)
        activity = (
            select(
                content_stream.c.circle_id,
                func.max(content_stream.c.published_time).label('last_active_time'),
                func.count()
                .filter(content_stream.c.published_time >= since)
                .label('recent_count'),
            )
            .group_by(content_stream.c.circle_id)
            .subquery()
        )
        active_value = func.coalesce(activity.c.last_active_time, HasnCircles.created_time)
        stmt = (
            select(
                HasnCircles,
                activity.c.last_active_time,
                func.coalesce(activity.c.recent_count, 0).label('recent_count'),
            )
            .outerjoin(activity, activity.c.circle_id == HasnCircles.circle_id)
            .where(
                HasnCircles.visibility == 'public',
                HasnCircles.status == 'active',
            )
        )
        if join_policy:
            stmt = stmt.where(HasnCircles.join_policy == join_policy)

        cursor_data = _decode_cursor(cursor)
        if cursor_data and cursor_data.get('sort') != sort:
            raise errors.RequestError(msg='圈子分页游标与排序方式不匹配')
        value_column: Any
        order_columns: tuple[Any, Any]
        cursor_value: int | datetime
        if sort == 'members':
            value_column = HasnCircles.member_count
            order_columns = (HasnCircles.member_count.desc(), HasnCircles.id.desc())
            if cursor_data:
                try:
                    cursor_value = int(cursor_data['value'])
                    cursor_id = int(cursor_data['id'])
                except (KeyError, TypeError, ValueError) as exc:
                    raise errors.RequestError(msg='圈子分页游标无效') from exc
                stmt = stmt.where(
                    or_(
                        value_column < cursor_value,
                        and_(value_column == cursor_value, HasnCircles.id < cursor_id),
                    )
                )
        else:
            value_column = HasnCircles.created_time if sort == 'newest' else active_value
            order_columns = (value_column.desc(), HasnCircles.id.desc())
            if cursor_data:
                try:
                    cursor_value = datetime.fromisoformat(str(cursor_data['value']))
                    cursor_id = int(cursor_data['id'])
                except (KeyError, TypeError, ValueError) as exc:
                    raise errors.RequestError(msg='圈子分页游标无效') from exc
                stmt = stmt.where(
                    or_(
                        value_column < cursor_value,
                        and_(value_column == cursor_value, HasnCircles.id < cursor_id),
                    )
                )

        rows = (await db.execute(stmt.order_by(*order_columns).limit(limit + 1))).all()
        has_more = len(rows) > limit
        rows = rows[:limit]
        items: list[dict[str, Any]] = []
        for row in rows:
            item = CircleService._circle_dict(row.HasnCircles)
            item['recent_count'] = int(row.recent_count or 0)
            item['last_active_time'] = (
                row.last_active_time.isoformat() if row.last_active_time else None
            )
            items.append(item)
        await CircleService._enrich_discovery(
            db,
            items=items,
            viewer_hasn_id=viewer_hasn_id,
        )
        next_cursor = None
        if has_more and rows:
            last = rows[-1]
            if sort == 'members':
                value: int | str = last.HasnCircles.member_count
            elif sort == 'newest':
                value = last.HasnCircles.created_time.isoformat()
            else:
                value = (
                    last.last_active_time or last.HasnCircles.created_time
                ).isoformat()
            next_cursor = _encode_cursor(
                {'sort': sort, 'value': value, 'id': last.HasnCircles.id}
            )
        return {'items': items, 'next_cursor': next_cursor}

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
        # 圈闸之上每条内容还要过自己的 visibility 判据：圈成员身份不豁免
        # followers（仍需关注作者）与 private（仅作者/主人可见）——此前完全不看，
        # 公开圈里的私密/followers 内容会漏给所有成员，匿名走 open 面同样能读到。
        # open 面（public_only=True）按匿名 viewer 判权，只剩 public。
        content_viewer = None if public_only else viewer_hasn_id
        stream = union_all(
            select(
                literal('post').label('content_type'),
                HasnPosts.post_id.label('content_id'),
                HasnPosts.published_time.label('sort_time'),
            ).where(
                HasnPosts.circle_id == c.circle_id,
                HasnPosts.status == 'published',
                HasnPosts.published_time.is_not(None),
                content_visibility_sql(HasnPosts, viewer_hasn_id=content_viewer),
            ),
            select(
                literal('article').label('content_type'),
                HasnArticles.article_id.label('content_id'),
                HasnArticles.published_time.label('sort_time'),
            ).where(
                HasnArticles.circle_id == c.circle_id,
                HasnArticles.status == 'published',
                HasnArticles.published_time.is_not(None),
                content_visibility_sql(HasnArticles, viewer_hasn_id=content_viewer),
            ),
        ).subquery()
        stmt = select(stream)
        cursor_data = _decode_cursor(cursor)
        if cursor_data:
            try:
                cursor_time = datetime.fromisoformat(str(cursor_data['time']))
                cursor_type = str(cursor_data['type'])
                cursor_id = str(cursor_data['id'])
            except (KeyError, TypeError, ValueError) as exc:
                raise errors.RequestError(msg='圈子内容分页游标无效') from exc
            if cursor_type not in {'post', 'article'} or not cursor_id:
                raise errors.RequestError(msg='圈子内容分页游标无效')
            stmt = stmt.where(
                or_(
                    stream.c.sort_time < cursor_time,
                    and_(
                        stream.c.sort_time == cursor_time,
                        stream.c.content_type < cursor_type,
                    ),
                    and_(
                        stream.c.sort_time == cursor_time,
                        stream.c.content_type == cursor_type,
                        stream.c.content_id < cursor_id,
                    ),
                )
            )
        rows = (
            await db.execute(
                stmt.order_by(
                    stream.c.sort_time.desc(),
                    stream.c.content_type.desc(),
                    stream.c.content_id.desc(),
                ).limit(limit + 1)
            )
        ).mappings().all()
        has_more = len(rows) > limit
        rows = rows[:limit]
        post_ids = [row['content_id'] for row in rows if row['content_type'] == 'post']
        article_ids = [row['content_id'] for row in rows if row['content_type'] == 'article']
        # 卡片层再叠一次同一判据（纵深防御：union 已过滤，这里防未来调用方直接塞 id）
        post_cards = await fetch_post_cards(db, post_ids, viewer_hasn_id=content_viewer)
        article_cards = await fetch_article_cards(db, article_ids, viewer_hasn_id=content_viewer)
        card_maps = {'post': post_cards, 'article': article_cards}
        page = [
            card_maps[row['content_type']][row['content_id']]
            for row in rows
            if row['content_id'] in card_maps[row['content_type']]
        ]
        next_cursor = None
        if has_more and rows:
            last = rows[-1]
            next_cursor = _encode_cursor(
                {
                    'time': last['sort_time'].isoformat(),
                    'type': last['content_type'],
                    'id': last['content_id'],
                }
            )
        membership = (
            await CircleService._membership(db, c.circle_id, viewer_hasn_id)
            if viewer_hasn_id
            else None
        )
        return {
            'circle': CircleService._circle_dict(
                c,
                my_role=membership.role if membership else None,
                my_status=membership.status if membership else None,
            ),
            'items': page,
            'next_cursor': next_cursor,
        }

    @staticmethod
    async def list_pending_content(
        db: AsyncSession,
        *,
        ident: str,
        actor_hasn_id: str,
        cursor: str | None = None,
        limit: int = 20,
    ) -> dict[str, Any]:
        """分页列出圈内待审帖子和文章，仅圈主/管理员可读。"""
        c = await CircleService._get(db, ident)
        if not c:
            raise errors.NotFoundError(msg='圈子不存在')
        await CircleService._assert_manager(db, c.circle_id, actor_hasn_id)
        stream = union_all(
            select(
                literal('post').label('content_type'),
                HasnPosts.post_id.label('content_id'),
                HasnPosts.created_time.label('sort_time'),
            ).where(
                HasnPosts.circle_id == c.circle_id,
                HasnPosts.status == 'pending_review',
            ),
            select(
                literal('article').label('content_type'),
                HasnArticles.article_id.label('content_id'),
                HasnArticles.created_time.label('sort_time'),
            ).where(
                HasnArticles.circle_id == c.circle_id,
                HasnArticles.status == 'pending_review',
            ),
        ).subquery()
        stmt = select(stream)
        cursor_data = _decode_cursor(cursor)
        if cursor_data:
            try:
                cursor_time = datetime.fromisoformat(str(cursor_data['time']))
                cursor_type = str(cursor_data['type'])
                cursor_id = str(cursor_data['id'])
            except (KeyError, TypeError, ValueError) as exc:
                raise errors.RequestError(msg='待审内容分页游标无效') from exc
            stmt = stmt.where(
                or_(
                    stream.c.sort_time < cursor_time,
                    and_(
                        stream.c.sort_time == cursor_time,
                        stream.c.content_type < cursor_type,
                    ),
                    and_(
                        stream.c.sort_time == cursor_time,
                        stream.c.content_type == cursor_type,
                        stream.c.content_id < cursor_id,
                    ),
                )
            )
        rows = (
            await db.execute(
                stmt.order_by(
                    stream.c.sort_time.desc(),
                    stream.c.content_type.desc(),
                    stream.c.content_id.desc(),
                ).limit(limit + 1)
            )
        ).mappings().all()
        has_more = len(rows) > limit
        rows = rows[:limit]
        post_cards = await fetch_post_cards(
            db,
            [row['content_id'] for row in rows if row['content_type'] == 'post'],
        )
        article_cards = await fetch_article_cards(
            db,
            [row['content_id'] for row in rows if row['content_type'] == 'article'],
        )
        card_maps = {'post': post_cards, 'article': article_cards}
        items: list[dict[str, Any]] = []
        for row in rows:
            card = card_maps[row['content_type']].get(row['content_id'])
            if card is None:
                continue
            card['status'] = 'pending_review'
            card['submitted_time'] = row['sort_time'].isoformat()
            items.append(card)
        next_cursor = None
        if has_more and rows:
            last = rows[-1]
            next_cursor = _encode_cursor(
                {
                    'time': last['sort_time'].isoformat(),
                    'type': last['content_type'],
                    'id': last['content_id'],
                }
            )
        return {'items': items, 'next_cursor': next_cursor}

    @staticmethod
    async def moderate_content(db: AsyncSession, *, ident: str, content_type: str, content_id: str, actor_hasn_id: str, action: str) -> dict[str, Any]:
        c = await CircleService._get(db, ident)
        if not c:
            raise errors.NotFoundError(msg='圈子不存在')
        await CircleService._assert_manager(db, c.circle_id, actor_hasn_id)
        obj: HasnPosts | HasnArticles | None
        if content_type == 'post':
            obj = (
                await db.execute(
                    select(HasnPosts).where(
                        HasnPosts.post_id == content_id,
                        HasnPosts.circle_id == c.circle_id,
                    )
                )
            ).scalars().first()
        elif content_type == 'article':
            obj = (
                await db.execute(
                    select(HasnArticles).where(
                        HasnArticles.article_id == content_id,
                        HasnArticles.circle_id == c.circle_id,
                    )
                )
            ).scalars().first()
        else:
            raise errors.RequestError(msg='未知圈内内容类型')
        if not obj:
            raise errors.NotFoundError(msg='圈内内容不存在')
        previous_status = obj.status
        if action == 'approve':
            obj.status = 'published'
            if obj.published_time is None:
                obj.published_time = timezone.now()
        elif action == 'hide':
            obj.status = 'hidden'
        elif action == 'delete':
            obj.status = 'deleted'
        else:
            raise errors.RequestError(msg='未知内容治理动作')
        if previous_status != 'published' and obj.status == 'published':
            await CircleService.bump_content_count(db, circle_id=c.circle_id)
        elif previous_status == 'published' and obj.status != 'published':
            await db.execute(
                update(HasnCircles)
                .where(HasnCircles.circle_id == c.circle_id)
                .values(content_count=func.greatest(HasnCircles.content_count - 1, 0))
            )
        await db.flush()
        if previous_status != obj.status:
            event_type = {
                'approve': 'circle_content_approved',
                'hide': 'circle_content_hidden',
                'delete': 'circle_content_deleted',
            }[action]
            title = {
                'approve': f'你在「{c.name}」发布的内容已通过审核',
                'hide': f'你在「{c.name}」发布的内容已被隐藏',
                'delete': f'你在「{c.name}」发布的内容已被删除',
            }[action]
            await notification_service.notify_circle_event(
                db,
                recipient_hasn_id=obj.author_hasn_id,
                actor_hasn_id=actor_hasn_id,
                ntype=event_type,
                title=title,
                circle_id=c.circle_id,
                circle_name=c.name,
                content_type=content_type,
                content_id=content_id,
                recipient_type=obj.author_type,
                recipient_owner_hasn_id=obj.owner_hasn_id,
            )
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

    @staticmethod
    async def notify_pending_content(
        db: AsyncSession,
        *,
        circle: HasnCircles,
        author_hasn_id: str,
        content_type: str,
        content_id: str,
    ) -> None:
        """通知圈子管理者有新内容待审。"""
        await CircleService._notify_managers(
            db,
            circle=circle,
            actor_hasn_id=author_hasn_id,
            ntype='circle_content_pending',
            title=f'「{circle.name}」有新内容待审核',
            content_type=content_type,
            content_id=content_id,
        )


circle_service = CircleService()
