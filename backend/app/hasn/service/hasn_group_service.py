"""HASN 群组编排 service（建群 / 群管理）。

不同于 codegen 生成的 `hasn_group_members_service`（裸 CRUD），本 service 承载
**建群与群管理的业务编排**：分配协议层 `group_id`（g:NNNNNN）、以 type=group 写
`hasn_conversations` 主表、seed `hasn_group_members`、维护 member_count、判角色权限。

事实源：docs/hasn-node设计文档/03-Runtime调度/06-群聊派发与Agent参与设计.md G1。
权威性：云端是群结构唯一权威；daemon/webui 只做镜像与展示。
"""
from __future__ import annotations

from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.hasn.model.hasn_agents import HasnAgents
from backend.app.hasn.model.hasn_conversations import HasnConversations
from backend.app.hasn.model.hasn_group_members import HasnGroupMembers
from backend.app.hasn.model.hasn_humans import HasnHumans
from backend.common.exception import errors
from backend.utils.timezone import timezone

VALID_AGENT_POLICY = {'free', 'mention_only', 'silent', 'no_agent'}
_GROUP_ID_BASE = 500000
_MAX_MEMBERS_DEFAULT = 200
_ADMIN_ROLES = ('owner', 'admin')
# 群列表里每个群附带的头像预览成员上限（前 N 个成员，供 WebUI 拼九宫格群头像）。
# 取 9 与详情名册一致：>4 人时列表与详情都拼 3×3 九宫格、显示同一批前 9 个成员（同序）。
_AVATAR_PREVIEW_LIMIT = 9


class HasnGroupService:
    """群组建/管编排（云端权威）。"""

    # ─── id 分配 ───
    @staticmethod
    async def _alloc_group_id(db: AsyncSession) -> str:
        """分配 g:NNNNNN（取已有最大数字 +1，基线 500000）。

        可移植 PG/SQLite；建群低频，事务内 max+1 足够。并发极端下靠
        `hasn_conversations` 唯一性兜底（如需更强可后续上 Postgres 序列）。
        """
        rows = await db.execute(
            select(HasnConversations.group_id).where(HasnConversations.group_id.like('g:%'))
        )
        mx = _GROUP_ID_BASE
        for (gid,) in rows.all():
            try:
                mx = max(mx, int(str(gid).split(':', 1)[1]))
            except (ValueError, IndexError):
                continue
        return f'g:{mx + 1}'

    # ─── 成员元信息解析 ───
    @staticmethod
    async def _resolve_meta(db: AsyncSession, hasn_id: str) -> tuple[str, str, str]:
        """返回 (member_type, member_name, member_star_id)。查不到则降级用 hasn_id。"""
        if hasn_id.startswith('a_'):
            agent = (
                await db.execute(select(HasnAgents).where(HasnAgents.hasn_id == hasn_id))
            ).scalar_one_or_none()
            if agent:
                return 'agent', agent.display_name or hasn_id, agent.star_id or ''
            return 'agent', hasn_id, ''
        human = (
            await db.execute(select(HasnHumans).where(HasnHumans.hasn_id == hasn_id))
        ).scalar_one_or_none()
        if human:
            return 'human', human.nickname or hasn_id, human.star_id or ''
        return 'human', hasn_id, ''

    # ─── 序列化 ───
    @staticmethod
    def _member_to_dict(m: HasnGroupMembers) -> dict[str, Any]:
        return {
            'hasn_id': m.member_id,
            'member_type': m.member_type,
            'display_name': m.member_name,
            'star_id': m.member_star_id,
            'role': m.role,
            'muted': m.muted,
            'joined_at': m.joined_at.isoformat() if m.joined_at else None,
        }

    @classmethod
    def _group_to_dict(
        cls, conv: HasnConversations, members: list[HasnGroupMembers] | None = None
    ) -> dict[str, Any]:
        data: dict[str, Any] = {
            'group_id': conv.group_id,
            'conversation_id': str(conv.id),
            'title': conv.group_name,
            'avatar_url': conv.group_avatar_url,
            'owner_id': conv.group_owner_id,
            'agent_policy': conv.agent_policy,
            'join_policy': conv.join_policy,
            'max_members': conv.max_members,
            'member_count': conv.member_count,
            'status': conv.status,
        }
        if members is not None:
            data['members'] = [cls._member_to_dict(m) for m in members]
        return data

    # ─── 内部 helper ───
    @staticmethod
    async def _get_group_or_404(db: AsyncSession, group_id: str) -> HasnConversations:
        conv = (
            await db.execute(
                select(HasnConversations).where(
                    HasnConversations.type == 'group',
                    HasnConversations.group_id == group_id,
                )
            )
        ).scalar_one_or_none()
        if not conv or conv.status == 'disbanded':
            raise errors.NotFoundError(msg='群组不存在或已解散')
        return conv

    @staticmethod
    async def _load_members(db: AsyncSession, conv_id: Any) -> list[HasnGroupMembers]:
        # 按入群时间升序：与列表 members_preview 同序，保证列表/详情群头像宫格成员一致。
        return list(
            (
                await db.execute(
                    select(HasnGroupMembers)
                    .where(HasnGroupMembers.conversation_id == conv_id)
                    .order_by(HasnGroupMembers.joined_at.asc())
                )
            )
            .scalars()
            .all()
        )

    @staticmethod
    def _role_of(members: list[HasnGroupMembers], hasn_id: str) -> str | None:
        for m in members:
            if m.member_id == hasn_id:
                return m.role
        return None

    @classmethod
    async def _add_member_row(
        cls,
        db: AsyncSession,
        conv_id: Any,
        hasn_id: str,
        *,
        role: str,
        invited_by: str | None,
        now: Any,
        roster: list[HasnGroupMembers],
    ) -> HasnGroupMembers:
        mtype, mname, mstar = await cls._resolve_meta(db, hasn_id)
        row = HasnGroupMembers(
            conversation_id=conv_id,
            member_id=hasn_id,
            member_type=mtype,
            member_star_id=mstar,
            member_name=mname,
            role=role,
            muted=False,
            joined_at=now,
            invited_by=invited_by,
        )
        db.add(row)
        roster.append(row)
        return row

    # ─── 对外编排 ───
    @classmethod
    async def create_group(
        cls,
        db: AsyncSession,
        *,
        owner_hasn_id: str,
        title: str,
        members: list[dict[str, Any]] | None = None,
        agent_policy: str = 'free',
        avatar_url: str | None = None,
    ) -> dict[str, Any]:
        """建群：分配 g:NNNNNN + 写 type=group 会话 + seed 成员（创建者 role=owner）。"""
        title = (title or '').strip()
        if not 1 <= len(title) <= 80:
            raise errors.RequestError(msg='群名称需 1..80 字')
        if agent_policy not in VALID_AGENT_POLICY:
            raise errors.RequestError(msg=f'agent_policy 非法: {agent_policy}')

        member_ids: list[str] = []
        seen: set[str] = {owner_hasn_id}
        for m in members or []:
            mid = (m.get('hasn_id') or '').strip()
            if mid and mid not in seen:
                seen.add(mid)
                member_ids.append(mid)

        now = timezone.now()
        group_id = await cls._alloc_group_id(db)
        owner_type, _, _ = await cls._resolve_meta(db, owner_hasn_id)

        conv = HasnConversations(
            type='group',
            group_id=group_id,
            group_name=title,
            group_owner_id=owner_hasn_id,
            group_avatar_url=avatar_url,
            agent_policy=agent_policy,
            join_policy='invite_only',
            max_members=_MAX_MEMBERS_DEFAULT,
            allow_invite=True,
            mute_all=False,
            participant_a_id=owner_hasn_id,
            participant_a_type=owner_type,
            relation_type='social',
            status='active',
            member_count=0,
        )
        db.add(conv)
        await db.flush()

        roster: list[HasnGroupMembers] = []
        await cls._add_member_row(
            db, conv.id, owner_hasn_id, role='owner', invited_by=None, now=now, roster=roster
        )
        for mid in member_ids:
            await cls._add_member_row(
                db, conv.id, mid, role='member', invited_by=owner_hasn_id, now=now, roster=roster
            )
        conv.member_count = len(roster)
        await db.flush()
        return cls._group_to_dict(conv, roster)

    @classmethod
    async def list_my_groups(cls, db: AsyncSession, *, hasn_id: str) -> list[dict[str, Any]]:
        """列出 hasn_id 作为成员的活跃群（摘要 + 头像预览名册，不含完整名册）。

        每个群附带 `members_preview`（按入群时间取前 `_AVATAR_PREVIEW_LIMIT` 个成员），
        供 WebUI 在消息列表拼九宫格群头像；完整名册仍需走 `get_group_detail`。
        """
        conv_ids = (
            (
                await db.execute(
                    select(HasnGroupMembers.conversation_id).where(
                        HasnGroupMembers.member_id == hasn_id
                    )
                )
            )
            .scalars()
            .all()
        )
        if not conv_ids:
            return []
        convs = (
            (
                await db.execute(
                    select(HasnConversations).where(
                        HasnConversations.id.in_(conv_ids),
                        HasnConversations.type == 'group',
                        HasnConversations.status == 'active',
                    )
                )
            )
            .scalars()
            .all()
        )
        # 单次批量取所有群成员（避免逐群 N+1），按入群时间升序便于取前几个作头像。
        active_ids = [c.id for c in convs]
        preview_by_conv: dict[Any, list[HasnGroupMembers]] = {}
        if active_ids:
            roster_rows = (
                (
                    await db.execute(
                        select(HasnGroupMembers)
                        .where(HasnGroupMembers.conversation_id.in_(active_ids))
                        .order_by(HasnGroupMembers.joined_at.asc())
                    )
                )
                .scalars()
                .all()
            )
            for m in roster_rows:
                bucket = preview_by_conv.setdefault(m.conversation_id, [])
                if len(bucket) < _AVATAR_PREVIEW_LIMIT:
                    bucket.append(m)
        result: list[dict[str, Any]] = []
        for c in convs:
            data = cls._group_to_dict(c)
            data['members_preview'] = [
                cls._member_to_dict(m) for m in preview_by_conv.get(c.id, [])
            ]
            result.append(data)
        return result

    @classmethod
    async def get_group_detail(
        cls, db: AsyncSession, *, hasn_id: str, group_id: str
    ) -> dict[str, Any]:
        """群详情 + 名册。要求 hasn_id 是群成员。"""
        conv = await cls._get_group_or_404(db, group_id)
        members = await cls._load_members(db, conv.id)
        if cls._role_of(members, hasn_id) is None:
            raise errors.ForbiddenError(msg='非群成员，无权查看')
        return cls._group_to_dict(conv, members)

    @classmethod
    async def add_members(
        cls,
        db: AsyncSession,
        *,
        actor_hasn_id: str,
        group_id: str,
        members: list[dict[str, Any]],
    ) -> dict[str, Any]:
        conv = await cls._get_group_or_404(db, group_id)
        roster = await cls._load_members(db, conv.id)
        # 加人放开给任意群成员（对齐微信默认：群成员皆可拉人）——只拒非成员。
        # 移除成员 / 改群设置 / 解散仍受 owner/admin 限制（见下）。
        if cls._role_of(roster, actor_hasn_id) is None:
            raise errors.ForbiddenError(msg='非群成员，无权加人')
        existing_ids = {m.member_id for m in roster}
        now = timezone.now()
        for m in members or []:
            mid = (m.get('hasn_id') or '').strip()
            if mid and mid not in existing_ids:
                existing_ids.add(mid)
                await cls._add_member_row(
                    db, conv.id, mid, role='member', invited_by=actor_hasn_id, now=now, roster=roster
                )
        conv.member_count = len(roster)
        await db.flush()
        return cls._group_to_dict(conv, roster)

    @classmethod
    async def remove_member(
        cls, db: AsyncSession, *, actor_hasn_id: str, group_id: str, member_id: str
    ) -> dict[str, Any]:
        """踢人（owner/admin）或自己退群。群主不可被移除（须先转让/解散）。"""
        conv = await cls._get_group_or_404(db, group_id)
        roster = await cls._load_members(db, conv.id)
        actor_role = cls._role_of(roster, actor_hasn_id)
        if actor_role is None:
            raise errors.ForbiddenError(msg='非群成员')
        if actor_hasn_id != member_id and actor_role not in _ADMIN_ROLES:
            raise errors.ForbiddenError(msg='仅群主/管理员可移除成员')
        if member_id == conv.group_owner_id:
            raise errors.RequestError(msg='群主不可被移除，请先转让或解散群')
        target = next((m for m in roster if m.member_id == member_id), None)
        if not target:
            raise errors.NotFoundError(msg='成员不存在')
        await db.delete(target)
        conv.member_count = max(0, (conv.member_count or 1) - 1)
        await db.flush()
        return {'group_id': group_id, 'removed': member_id, 'member_count': conv.member_count}

    @classmethod
    async def update_group(
        cls,
        db: AsyncSession,
        *,
        actor_hasn_id: str,
        group_id: str,
        title: str | None = None,
        avatar_url: str | None = None,
        agent_policy: str | None = None,
    ) -> dict[str, Any]:
        conv = await cls._get_group_or_404(db, group_id)
        roster = await cls._load_members(db, conv.id)
        if cls._role_of(roster, actor_hasn_id) not in _ADMIN_ROLES:
            raise errors.ForbiddenError(msg='仅群主/管理员可改群设置')
        if title is not None:
            t = title.strip()
            if not 1 <= len(t) <= 80:
                raise errors.RequestError(msg='群名称需 1..80 字')
            conv.group_name = t
        if avatar_url is not None:
            conv.group_avatar_url = avatar_url
        if agent_policy is not None:
            if agent_policy not in VALID_AGENT_POLICY:
                raise errors.RequestError(msg=f'agent_policy 非法: {agent_policy}')
            conv.agent_policy = agent_policy
        await db.flush()
        return cls._group_to_dict(conv, roster)

    @classmethod
    async def disband_group(
        cls, db: AsyncSession, *, actor_hasn_id: str, group_id: str
    ) -> dict[str, Any]:
        conv = await cls._get_group_or_404(db, group_id)
        if actor_hasn_id != conv.group_owner_id:
            raise errors.ForbiddenError(msg='仅群主可解散群')
        conv.status = 'disbanded'
        await db.flush()
        return {'group_id': group_id, 'status': 'disbanded'}


hasn_group_service: HasnGroupService = HasnGroupService()
